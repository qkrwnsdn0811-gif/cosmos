"""CLI validation and opt-in database writes without any network access."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from services.document_loader import cli


SOURCE_URI = "hdfs://cluster:9000/data/news/topic=news.raw/partition=0/start=00000000000000000000"


def payload():
    content = "A collected article body which must never appear in CLI summaries."
    return {"schema_version": 1, "event_id": "news:test:1", "source": "test_provider",
            "region": "overseas", "language": "en", "url": "https://example.test/article?id=1",
            "title": "Public announcement", "content": content, "organization": "Example publisher",
            "published_at": None, "collected_at": "2026-09-16T00:00:00Z", "run_id": "test-run",
            "content_hash": hashlib.sha256(content.encode()).hexdigest(), "metadata": {}}


def batch():
    return {"format": "news", "input_uri": SOURCE_URI, "batch_id": "batch-1", "manifest_sha256": "a" * 64,
            "records": [{"kind": "news", "record": payload(), "raw_uri": SOURCE_URI + "/region=overseas/date=2026-09-16/part.parquet"}],
            "verification": {"metadata_hashes_verified": True}}


class CLITest(unittest.TestCase):
    def args(self, command="validate", *extra):
        return cli.parser().parse_args([command, "--kind", "news", "--input", "example-snapshot", *extra])

    def test_validate_does_not_require_a_database_or_open_a_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "load_source", return_value=batch()), \
                patch("psycopg.connect") as connect, patch.object(cli, "load_documents") as load:
            result = cli.execute(self.args())
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["document_types"], {"NEWS": 1})
        connect.assert_not_called()
        load.assert_not_called()

    def test_load_defaults_to_real_transaction_rollback_and_no_source_registration(self):
        connection = MagicMock()
        with patch.dict(os.environ, {"DOCUMENT_DATABASE_URL": "postgresql://example/test"}, clear=True), \
                patch.object(cli, "load_source", return_value=batch()), \
                patch("psycopg.connect", return_value=connection) as connect, \
                patch.object(cli, "load_documents", return_value={"already_loaded": False}) as load:
            result = cli.execute(self.args("load"))
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(load.call_args.kwargs["commit"])
        self.assertFalse(load.call_args.kwargs["register_sources"])
        self.assertIs(load.call_args.args[0], connection.__enter__.return_value)
        connect.assert_called_once_with("postgresql://example/test", connect_timeout=10)

    def test_commit_and_source_registration_require_explicit_flags(self):
        with patch.dict(os.environ, {"DOCUMENT_DATABASE_URL": "postgresql://example/test"}, clear=True), \
                patch.object(cli, "load_source", return_value=batch()), patch("psycopg.connect"), \
                patch.object(cli, "load_documents", return_value={"already_loaded": False}) as load:
            result = cli.execute(self.args("load", "--commit", "--register-sources"))
        self.assertEqual(result["status"], "loaded")
        self.assertTrue(load.call_args.kwargs["commit"])
        self.assertTrue(load.call_args.kwargs["register_sources"])

    def test_committed_replay_has_already_loaded_status(self):
        with patch.dict(os.environ, {"DOCUMENT_DATABASE_URL": "postgresql://example/test"}, clear=True), \
                patch.object(cli, "load_source", return_value=batch()), patch("psycopg.connect"), \
                patch.object(cli, "load_documents", return_value={"already_loaded": True}):
            self.assertEqual(cli.execute(self.args("load", "--commit"))["status"], "already_loaded")

    def test_missing_dsn_returns_actionable_error_without_connection_attempt(self):
        error = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), patch.object(cli, "load_source", return_value=batch()), \
                patch("psycopg.connect") as connect, redirect_stderr(error):
            code = cli.main(["load", "--kind", "news", "--input", "example-snapshot"])
        self.assertEqual(code, 2)
        self.assertIn("DOCUMENT_DATABASE_URL", json.loads(error.getvalue())["message"])
        connect.assert_not_called()

    def test_database_connection_error_never_prints_dsn_or_password(self):
        secret = "do-not-print-this-password"
        dsn = "postgresql://user:" + secret + "@example/test"
        output, error = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"DOCUMENT_DATABASE_URL": dsn}, clear=True), \
                patch.object(cli, "load_source", return_value=batch()), \
                patch("psycopg.connect", side_effect=psycopg.OperationalError("connection failed: " + dsn)), \
                redirect_stdout(output), redirect_stderr(error):
            code = cli.main(["load", "--kind", "news", "--input", "example-snapshot"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn(secret, error.getvalue())
        self.assertNotIn(dsn, error.getvalue())
        self.assertEqual(json.loads(error.getvalue())["error_type"], "OperationalError")

    def test_invalid_source_payload_fails_before_database_connection(self):
        invalid = batch()
        invalid["records"][0]["record"]["content_hash"] = "0" * 64
        with patch.dict(os.environ, {"DOCUMENT_DATABASE_URL": "postgresql://example/test"}, clear=True), \
                patch.object(cli, "load_source", return_value=invalid), patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(ValueError, "content_hash"):
                cli.execute(self.args("load", "--commit"))
        connect.assert_not_called()

    def test_local_published_parquet_is_actually_read_without_database_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = payload()
            row = {**original, "kafka_topic": "news.raw", "kafka_partition": 0, "kafka_offset": 0,
                   "raw_json": json.dumps(original)}
            del row["metadata"]
            relative = "region=overseas/date=2026-09-16/part.parquet"
            target = root / relative
            target.parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist([row]), target)
            data = target.read_bytes()
            manifest = {"version": 1, "topic": "news.raw", "partition": 0, "start": 0, "end": 1,
                        "records": 1, "valid_records": 1, "invalid_records": 0, "cluster_id": "cluster",
                        "topic_id": "topic-id", "group": "writer", "files": [{"path": relative,
                        "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "records": 1}]}
            (root / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch("psycopg.connect") as connect, \
                    patch.object(cli, "load_documents") as load, redirect_stdout(output):
                code = cli.main(["validate", "--kind", "news", "--input", str(root), "--source-uri", SOURCE_URI])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["records"], 1)
            self.assertTrue(result["verification"]["metadata_hashes_verified"])
            self.assertNotIn(original["content"], output.getvalue())
            self.assertNotIn("load", result)
            connect.assert_not_called()
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
