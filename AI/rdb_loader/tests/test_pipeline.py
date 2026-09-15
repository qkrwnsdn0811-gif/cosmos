"""Producer -> Parquet -> loader CLI -> actual PostgreSQL, including exact retry."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from AI.rdb_loader.__main__ import main
from AI.rdb_loader.aggregate import aggregate_records, FORMULA_VERSION
from AI.rdb_loader.tests import test_postgres as database_fixture


@unittest.skipUnless(database_fixture.psycopg is not None and os.getenv("TEST_DATABASE_URL"),
                     "set TEST_DATABASE_URL for full pipeline integration")
class PipelineTest(unittest.TestCase):
    setUp = database_fixture.PostgresPublicationTests.setUp
    tearDown = database_fixture.PostgresPublicationTests.tearDown

    def test_feature_parquet_hdfs_download_cli_and_service_query(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        from psycopg.conninfo import make_conninfo

        features = [
            dict(source_company_id=str(self.a), target_company_id=str(self.b), relationship_type="SUPPLY",
                 document_id=str(uuid4()), document_type="NEWS", score=80, published_at="2026-09-14T00:00:00Z"),
            dict(source_company_id=str(self.a), target_company_id=str(self.b), relationship_type="SUPPLY",
                 document_id=str(uuid4()), document_type="DISCLOSURE", score=20, published_at="2026-08-26T00:00:00Z"),
            dict(source_company_id=str(self.a), target_company_id=str(self.c), relationship_type="SUPPLY",
                 document_id=str(uuid4()), document_type="DISCLOSURE", score=0, published_at="2026-08-26T00:00:00Z"),
        ]
        rows = aggregate_records(features, as_of_at="2026-09-15T00:00:00Z")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "published-input"
            root.mkdir()
            data_path = root / "part-000.parquet"
            pq.write_table(pa.Table.from_pylist(rows), data_path)
            snapshot = uuid4()
            uri = f"hdfs://master:9000/aggregated/{snapshot}"
            manifest = dict(schema_version=1, snapshot_mode="FULL", status="SUCCEEDED", snapshot_id=str(snapshot),
                            as_of_at="2026-09-15T00:00:00Z", formula_version=FORMULA_VERSION, model_version="test-feature-v1",
                            hdfs_uri=uri, windows=["7D", "30D", "90D"], record_count=6,
                            window_counts={"7D": 2, "30D": 2, "90D": 2},
                            files=[dict(path=data_path.name, sha256=hashlib.sha256(data_path.read_bytes()).hexdigest())])
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "_SUCCESS").touch()
            database_url = make_conninfo(os.environ["TEST_DATABASE_URL"], options=f"-csearch_path={self.schema}")

            def hdfs_download(command, **kwargs):
                self.assertEqual(command[1:4], ["dfs", "-get", uri])
                shutil.copytree(root, command[4])
                return SimpleNamespace(returncode=0)

            with patch.dict(os.environ, {"DATABASE_URL": database_url}), patch("AI.rdb_loader.source.subprocess.run", side_effect=hdfs_download):
                self.assertEqual(main(["--input", uri]), 0)
                self.assertEqual(main(["--input", uri]), 0)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM relationship_score_history").fetchone()[0], 5)
            self.assertEqual(self.connection.execute("SELECT record_count, stored_count FROM graph_snapshot_load").fetchone(), (6, 5))
            self.assertEqual(self.connection.execute(
                "SELECT snapshot_id FROM graph_snapshot WHERE status='PUBLISHED' ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1"
            ).fetchone(), (snapshot,))
            # The existing graph API selects this snapshot's 30D edges; it gets
            # the two components and the shared score without user preferences.
            values = self.connection.execute("""
                SELECT cr.target_company_id, rsc.news_score, rsc.disclosure_score, rsc.score
                FROM relationship_score_current rsc
                JOIN company_relationship cr USING (relationship_id)
                WHERE rsc.snapshot_id = %s AND rsc.window_type = '30D'
                ORDER BY cr.target_company_id
                """, (snapshot,)).fetchall()
            self.assertEqual(values, [(self.b, 80, 20, 50), (self.c, None, 0, 0)])
            # A failed completed-input check never touches the already published graph.
            (root / "_SUCCESS").unlink()
            with patch.dict(os.environ, {"DATABASE_URL": database_url}):
                self.assertEqual(main(["--input", str(root)]), 1)
            self.assertEqual(self.connection.execute("SELECT count(*) FROM relationship_score_current").fetchone()[0], 5)


if __name__ == "__main__":
    unittest.main()
