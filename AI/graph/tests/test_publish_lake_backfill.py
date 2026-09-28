"""Pure metadata and crash-recovery checks; real Spark/HDFS is a server smoke test."""
import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "spark"))
import publish_lake_backfill as publisher


class FakeHdfs:
    def __init__(self, files):
        self.contents = copy.deepcopy(files)
        self.renamed = []
        self.crash_after_rename = False

    def exists(self, path):
        return path in self.contents

    def rename_new(self, source, destination):
        if destination in self.contents:
            raise FileExistsError(destination)
        self.contents[destination] = self.contents.pop(source)
        self.renamed.append((source, destination))
        if self.crash_after_rename:
            self.crash_after_rename = False
            raise RuntimeError("process stopped after atomic rename")

    def files(self, path):
        prefix = path + "/"
        return sorted([{"relative_path": name[len(prefix):], **info}
                       for name, info in self.contents.items() if name.startswith(prefix)],
                      key=lambda item: item["relative_path"])


class BackfillPublisherTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
        self.artifacts.mkdir(exist_ok=True)
        self.root = self.artifacts / ("publisher-tests-" + uuid.uuid4().hex)
        self.root.mkdir()
        (self.root / "parts").mkdir()
        self.source = self.root / "parts" / "one.jsonl"
        self.source.write_text('{"id":"a"}\n', encoding="utf-8")
        (self.root / "source_manifest.json").write_text('{"frozen":true}', encoding="utf-8")
        self.dataset = {"name": "cleaned_news", "target_root": "/data-lake/cleaned/news", "dataset_path": "data",
                        "schema": {"type": "struct", "fields": [{"name": "id", "type": "string", "nullable": True, "metadata": {}}]},
                        "key_columns": ["id"], "row_count": 1,
                        "files": [{"local_file": "parts/one.jsonl", "sha256": publisher.file_sha256(self.source),
                                   "row_count": 1, "bytes": self.source.stat().st_size}]}
        self.manifest = {"schema_version": "lake-backfill-publication-1", "run_id": "test-run",
                         "as_of": "2026-09-16T00:00:00Z", "snapshot_id": str(uuid.uuid4()),
                         "source_manifest_sha256": publisher.file_sha256(self.root / "source_manifest.json"),
                         "datasets": [self.dataset]}

    def tearDown(self):
        resolved = self.root.resolve()
        if not resolved.is_relative_to(self.artifacts) or not resolved.name.startswith("publisher-tests-"):
            raise AssertionError("Refusing cleanup outside test workspace")
        shutil.rmtree(resolved)

    def write_manifest(self):
        path = self.root / "backfill_publication.json"
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.root / "finish.done.json").write_text(json.dumps({"publication_sha256": publisher.file_sha256(path)}), encoding="utf-8")

    def test_manifest_and_source_hash_contract(self):
        self.write_manifest()
        value, digest = publisher.validate_publication(self.root, "test-run")
        self.assertEqual(value, self.manifest)
        self.assertEqual(digest, publisher.file_sha256(self.root / "backfill_publication.json"))
        self.assertEqual(publisher.verify_source(self.root, self.dataset["files"][0]), self.source)
        self.source.write_text('{"id":"b"}\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA changed"):
            publisher.verify_source(self.root, self.dataset["files"][0])

    def test_changed_completion_marker_rejected(self):
        self.write_manifest()
        (self.root / "finish.done.json").write_text('{"publication_sha256":"wrong"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "completion marker"):
            publisher.validate_publication(self.root, "test-run")

    def test_bad_paths_roots_counts_and_schema_rejected(self):
        cases = [lambda: self.dataset["files"][0].update(local_file="../outside.jsonl"),
                 lambda: self.dataset.update(target_root="/data-lake/raw/news"),
                 lambda: self.dataset.update(dataset_path="../data"),
                 lambda: self.dataset.update(row_count=2),
                 lambda: self.dataset.update(key_columns=["missing"]),
                 lambda: self.dataset.update(schema=None)]
        original = copy.deepcopy(self.dataset)
        for change in cases:
            with self.subTest(change=change):
                self.dataset.clear()
                self.dataset.update(copy.deepcopy(original))
                change()
                self.write_manifest()
                with self.assertRaises(ValueError):
                    publisher.validate_publication(self.root, "test-run")

    def test_duplicate_source_and_destination_rejected(self):
        self.dataset["files"].append(copy.deepcopy(self.dataset["files"][0]))
        self.dataset["row_count"] = 2
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Repeated source"):
            publisher.validate_publication(self.root, "test-run")
        self.dataset["files"].pop()
        self.dataset["row_count"] = 1
        second = copy.deepcopy(self.dataset)
        second["name"] = "other"
        self.manifest["datasets"].append(second)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "Duplicate dataset destination"):
            publisher.validate_publication(self.root, "test-run")

    def test_immutable_metadata_and_schema_bound_checkpoint(self):
        path = self.root / "checkpoint.json"
        identity = publisher.shard_identity("a" * 64, self.dataset, self.dataset["files"][0], 0)
        checkpoint = {"identity": identity, "state": "SHARD_VERIFIED", "files": [
            {"relative_path": "chunk-000000-part-1.parquet", "attempt_relative_path": "part-1.parquet"}]}
        publisher.save_json(path, checkpoint)
        publisher.save_json(path, checkpoint)
        self.assertEqual(publisher.checked_checkpoint(path, identity), checkpoint)
        changed = copy.deepcopy(identity)
        changed["schema_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            publisher.checked_checkpoint(path, changed)
        with self.assertRaisesRegex(ValueError, "Immutable checkpoint conflicts"):
            publisher.save_json(path, {"different": True})
        self.assertEqual(publisher.read_json(path), checkpoint)

    @staticmethod
    def move_fixture():
        record = {"length": 50, "checksum_algorithm": "MD5-of-CRC32C", "checksum": "crc-ok"}
        prepared = {"attempt_path": "/attempt/data", "files": [
            {"relative_path": "chunk-000000-part.parquet", "attempt_relative_path": "part.parquet", **record}]}
        return record, prepared

    def test_recovery_after_file_rename_before_checkpoint(self):
        record, prepared = self.move_fixture()
        hdfs = FakeHdfs({"/attempt/data/part.parquet": record})
        hdfs.crash_after_rename = True
        with patch.object(publisher, "file_record", lambda fs, path: fs.contents[path]):
            with self.assertRaisesRegex(RuntimeError, "process stopped"):
                publisher.recover_moves(hdfs, prepared, "/flat")
            publisher.recover_moves(hdfs, prepared, "/flat")
            publisher.recover_moves(hdfs, prepared, "/flat", promoted=True)
        self.assertEqual(len(hdfs.renamed), 1)
        self.assertNotIn("/attempt/data/part.parquet", hdfs.contents)

    def test_bad_existing_file_and_missing_promoted_file_fail(self):
        record, prepared = self.move_fixture()
        hdfs = FakeHdfs({"/flat/chunk-000000-part.parquet": {**record, "checksum": "changed"}})
        with patch.object(publisher, "file_record", lambda fs, path: fs.contents[path]):
            with self.assertRaisesRegex(ValueError, "checksum/length changed"):
                publisher.recover_moves(hdfs, prepared, "/flat")
            hdfs.contents.clear()
            with self.assertRaisesRegex(ValueError, "lost a shard"):
                publisher.recover_moves(hdfs, prepared, "/flat", promoted=True)
        self.assertEqual(hdfs.renamed, [])

    def test_global_inventory_rejects_uncheckpointed_file(self):
        record, _ = self.move_fixture()
        hdfs = FakeHdfs({"/flat/part.parquet": record})
        expected = [{"relative_path": "part.parquet", **record}]
        publisher.verify_inventory(hdfs, "/flat", expected)
        hdfs.contents["/flat/uncheckpointed.parquet"] = record
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            publisher.verify_inventory(hdfs, "/flat", expected)

    def test_sandbox_mirrors_targets_and_global_commit_only(self):
        for logical in ["/data-lake/cleaned/news/run_id=smoke", "/data-lake/metadata/runs/run_id=smoke"]:
            self.assertEqual(publisher.actual_path(logical, "smoke", False), logical)
            mirrored = publisher.actual_path(logical, "smoke", True)
            self.assertEqual(mirrored, "/data-lake/sandbox/news/junwoo/backfill-validation/smoke" + logical)
            self.assertNotEqual(mirrored, logical)


if __name__ == "__main__":
    unittest.main()
