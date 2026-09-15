import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from AI.rdb_loader.__main__ import main
from AI.rdb_loader.contract import ContractError, WINDOWS, parse_manifest, validate_row
from AI.rdb_loader.source import local_snapshot, prepare_snapshot


NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)
SOURCE, TARGET = str(uuid4()), str(uuid4())


def row(window="7D", news=80, disclosure=20):
    score = (news + disclosure) / 2 if news is not None and disclosure is not None else (news if news is not None else disclosure)
    return dict(source_company_id=SOURCE, target_company_id=TARGET, relationship_type="SUPPLY", window_type=window,
                news_score=news, disclosure_score=disclosure, score=score, evidence_count=0 if score is None else 1,
                confidence=None, impact_direction=None, period_start=(NOW - timedelta(days=int(window[:-1]))).isoformat(), period_end=NOW.isoformat())


def write_snapshot(root, rows, *, parquet=False, complete=True):
    rows = list(rows)
    if complete:
        relationships = {}
        for item in rows:
            key = tuple(item[field] for field in ("source_company_id", "target_company_id", "relationship_type"))
            exemplar, present = relationships.setdefault(key, (item, set()))
            present.add(item["window_type"])
        for exemplar, present in relationships.values():
            end = datetime.fromisoformat(exemplar["period_end"])
            for window in WINDOWS:
                if window not in present:
                    rows.append(dict(exemplar, window_type=window,
                                     period_start=(end - timedelta(days=int(window[:-1]))).isoformat()))
    root.mkdir(parents=True, exist_ok=True)
    name = "part-000.parquet" if parquet else "part-000.jsonl"
    path = root / name
    if parquet:
        import pyarrow as pa
        import pyarrow.parquet as pq
        pq.write_table(pa.Table.from_pylist(rows), path)
    else:
        path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    manifest = dict(schema_version=1, status="SUCCEEDED", snapshot_mode="FULL", snapshot_id=str(uuid4()), as_of_at=NOW.isoformat(),
                    formula_version="test-v1", model_version="test-model", hdfs_uri="hdfs://master:9000/snapshots/test",
                    windows=list(WINDOWS), record_count=len(rows), window_counts={w: sum(r["window_type"] == w for r in rows) for w in WINDOWS},
                    files=[dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())])
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "_SUCCESS").touch()
    return manifest


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_parquet_null_zero_and_default_scores(self):
        write_snapshot(self.root, [row("7D", 0, None), row("30D", None, 70), row("90D")], parquet=True)
        with prepare_snapshot(self.root) as prepared:
            actual = list(prepared.rows())
            self.assertEqual([r["score"] for r in actual], [Decimal(0), Decimal(70), Decimal(50)])
            self.assertIsNone(actual[0]["disclosure_score"])
            self.assertIsNone(actual[1]["news_score"])
            self.assertEqual(list(prepared.rows()), actual)

    def test_both_null_is_validated_counted_and_omitted(self):
        write_snapshot(self.root, [row("7D", None, None), row("30D"), row("90D")])
        with prepare_snapshot(self.root) as prepared:
            self.assertEqual(prepared.manifest.record_count, 3)
            self.assertEqual(prepared.stored_count, 2)
            self.assertEqual(len(list(prepared.rows())), 2)

    def test_missing_success_rejected(self):
        write_snapshot(self.root, [row()])
        (self.root / "_SUCCESS").unlink()
        with self.assertRaisesRegex(ContractError, "_SUCCESS"):
            with prepare_snapshot(self.root): pass

    def test_corrupted_file_rejected(self):
        write_snapshot(self.root, [row()])
        with (self.root / "part-000.jsonl").open("a") as handle: handle.write("\n")
        with self.assertRaisesRegex(ContractError, "checksum"):
            with prepare_snapshot(self.root): pass

    def test_duplicate_rows_rejected(self):
        write_snapshot(self.root, [row(), row()])
        with self.assertRaisesRegex(ContractError, "duplicate"):
            with prepare_snapshot(self.root): pass

    def test_unlisted_file_rejected(self):
        write_snapshot(self.root, [row()])
        (self.root / "extra.jsonl").write_text("{}\n")
        with self.assertRaisesRegex(ContractError, "file list"):
            with prepare_snapshot(self.root): pass

    def test_count_mismatch_rejected(self):
        data = write_snapshot(self.root, [row()])
        data["record_count"] += 1
        data["window_counts"]["7D"] += 1
        (self.root / "manifest.json").write_text(json.dumps(data))
        with self.assertRaisesRegex(ContractError, "counts"):
            with prepare_snapshot(self.root): pass

    def test_missing_windows_rejected_even_when_manifest_counts_match(self):
        write_snapshot(self.root, [row()], complete=False)
        with self.assertRaisesRegex(ContractError, "every relationship"):
            with prepare_snapshot(self.root): pass
        with patch("psycopg.connect", side_effect=AssertionError("must not connect")):
            self.assertEqual(main(["--input", str(self.root)]), 1)

    def test_matching_global_window_counts_cannot_hide_incomplete_relationships(self):
        rows = [dict(row(window), target_company_id=str(uuid4())) for window in WINDOWS]
        data = write_snapshot(self.root, rows, complete=False)
        self.assertEqual(data["window_counts"], {window: 1 for window in WINDOWS})
        with self.assertRaisesRegex(ContractError, "every relationship"):
            with prepare_snapshot(self.root): pass

    def test_empty_requires_explicit_opt_in(self):
        write_snapshot(self.root, [row(news=None, disclosure=None)])
        with self.assertRaisesRegex(ContractError, "allow-empty"):
            with prepare_snapshot(self.root): pass
        with prepare_snapshot(self.root, allow_empty=True) as prepared:
            self.assertEqual(prepared.stored_count, 0)

    def test_local_source_changes_cannot_change_prepared_rows(self):
        write_snapshot(self.root, [row()])
        with prepare_snapshot(self.root) as prepared:
            (self.root / "part-000.jsonl").write_text("{}")
            self.assertEqual(next(prepared.rows())["score"], Decimal(50))

    def test_hdfs_identity_matches_manifest(self):
        write_snapshot(self.root, [row()])
        with self.assertRaisesRegex(ContractError, "differs"):
            with prepare_snapshot(self.root, input_uri="hdfs://master:9000/wrong"): pass

    def test_unsafe_manifest_path_rejected(self):
        for path in ("../x.jsonl", "/x.jsonl", "C:/x.jsonl", "data\\x.jsonl"):
            with self.subTest(path=path):
                data = write_snapshot(self.root, [row()])
                data["files"][0]["path"] = path
                with self.assertRaises(ContractError): parse_manifest(data)

    def test_invalid_score_count_period_and_window(self):
        manifest = parse_manifest(write_snapshot(self.root, [row()]))
        invalids = [dict(score=0), dict(news_score=float("nan")), dict(disclosure_score=float("inf")), dict(news_score=-1),
                    dict(score=101), dict(news_score=None), dict(evidence_count=0), dict(evidence_count=True),
                    dict(period_end="2026-09-15T00:00:00"), dict(window_type="1D"), dict(formula_version="wrong"),
                    dict(news_evidence_count=0), dict(confidence=2)]
        for update in invalids:
            with self.subTest(update=update):
                with self.assertRaises(ContractError): validate_row({**row(), **update}, manifest)

    def test_validate_only_never_connects(self):
        write_snapshot(self.root, [row()])
        with patch("psycopg.connect", side_effect=AssertionError("must not connect")):
            self.assertEqual(main(["--input", str(self.root), "--validate-only"]), 0)

    def test_hdfs_uses_argument_vector_and_rejects_globs(self):
        with patch("subprocess.run") as run:
            run.return_value.returncode = 0
            with local_snapshot("hdfs://master:9000/snapshots/one") as path:
                self.assertEqual(path.name, "snapshot")
            self.assertEqual(run.call_args.args[0][1:4], ["dfs", "-get", "hdfs://master:9000/snapshots/one"])
            self.assertNotIn("shell", run.call_args.kwargs)
        with self.assertRaises(ContractError):
            with local_snapshot("hdfs://master:9000/snapshots/*"): pass


if __name__ == "__main__":
    unittest.main()
