from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq


HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from realtime_analyzer import AnalysisError, RawArticle, analyze_batch, deduplicate_articles  # noqa: E402


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RealtimeAnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.raw = (self.base / "raw" / "topic=news.raw" / "partition=0" /
                    "start=00000000000000000000")
        self.output = self.base / "analyzed"
        self.aliases = HERE / "data" / "aliases.csv"
        self._write_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def _event(self, offset: int, title: str, content: str) -> tuple[dict, dict]:
        url = f"https://example.test/{offset}"
        content_hash = digest(content.encode())
        source = "fixture-news"
        event = {
            "schema_version": 1,
            "event_id": digest(f"{source}\n{url}\n{content_hash}".encode()),
            "source": source,
            "region": "domestic",
            "language": "ko",
            "url": url,
            "url_hash": digest(url.encode()),
            "title": title,
            "content": content,
            "content_hash": content_hash,
            "organization": "테스트신문",
            "published_at": "2026-09-16T00:00:00+00:00",
            "collected_at": "2026-09-16T01:00:00+00:00",
            "run_id": "fixture-run",
            "metadata": {"collector": "fixture"},
        }
        row = {
            key: event[key]
            for key in (
                "schema_version", "event_id", "source", "region", "language", "url", "title",
                "content", "organization", "published_at", "collected_at", "run_id",
            )
        }
        row.update(
            kafka_topic="news.raw",
            kafka_partition=0,
            kafka_offset=offset,
            raw_json=encoded(event).decode(),
        )
        return event, row

    def _write_fixture(self):
        self.raw.mkdir(parents=True)
        samsung_event, samsung = self._event(
            0,
            "삼성전자, 반도체 신제품 공개",
            "삼성전자는 새로운 반도체 제품을 공개하고 올해 생산 계획을 발표했다. " * 3,
        )
        _, unrelated = self._event(
            1,
            "서울 날씨 맑음",
            "서울의 오늘 날씨는 맑고 한낮 기온은 평년과 비슷할 것으로 전망된다. " * 3,
        )
        # Kafka at-least-once replay: same event identity, later collection and
        # offset.  The later delivery must be the one retained in analyzed data.
        replay_event = dict(samsung_event)
        replay_event.update(collected_at="2026-09-16T01:30:00+00:00", run_id="fixture-replay")
        replay = dict(samsung)
        replay.update(collected_at=replay_event["collected_at"], run_id=replay_event["run_id"],
                      kafka_offset=2, raw_json=encoded(replay_event).decode())
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist([samsung, unrelated, replay]), output)
        parquet = output.getvalue().to_pybytes()
        relative = "region=domestic/date=2026-09-16/part.parquet"
        path = self.raw / Path(*relative.split("/"))
        path.parent.mkdir(parents=True)
        path.write_bytes(parquet)
        manifest = {
            "version": 1,
            "cluster_id": "fixture-cluster",
            "topic_id": "fixture-topic-id",
            "topic": "news.raw",
            "group": "fixture-writer",
            "partition": 0,
            "start": 0,
            "end": 3,
            "records": 3,
            "valid_records": 3,
            "invalid_records": 0,
            "files": [{
                "path": relative,
                "bytes": len(parquet),
                "sha256": digest(parquet),
                "records": 3,
            }],
        }
        (self.raw / "_manifest.json").write_bytes(encoded(manifest))

    def run_analyzer(self, **overrides):
        values = {
            "input_uri": str(self.raw),
            "output_base": str(self.output),
            "aliases_path": self.aliases,
            "model_version": "dict-v1.3",
            "analysis_version": "test-revision",
            "analyzed_at": "2026-09-16T02:00:00+00:00",
        }
        values.update(overrides)
        return analyze_batch(**values)

    def test_publishes_all_valid_articles_including_empty_companies(self):
        result = self.run_analyzer()
        self.assertEqual(result["status"], "published")
        final = Path(result["output_uri"])
        self.assertTrue((final / "_SUCCESS").exists())
        self.assertEqual((final / "_SUCCESS").read_bytes(), b"")
        manifest = json.loads((final / "_manifest.json").read_bytes())
        self.assertEqual(manifest["output"]["records"], 2)
        self.assertEqual(manifest["output"]["duplicate_records"], 1)
        self.assertEqual(
            manifest["output"]["records"] + manifest["output"]["duplicate_records"],
            manifest["input"]["valid_records"],
        )
        self.assertEqual(manifest["output"]["docs_with_companies"], 1)
        rows = pq.read_table(final / "data.parquet").to_pylist()
        self.assertEqual([row["kafka_offset"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["companies"], [])
        self.assertEqual(rows[1]["companies"][0]["ticker"], "005930")
        self.assertEqual(rows[1]["collected_at"], "2026-09-16T01:30:00+00:00")
        self.assertEqual(json.loads(rows[1]["raw_json"])["run_id"], "fixture-replay")
        self.assertEqual(rows[0]["raw_manifest_sha256"], manifest["input"]["manifest_sha256"])
        self.assertEqual(json.loads(rows[0]["metadata_json"]), {"collector": "fixture"})
        self.assertIn("content", json.loads(rows[0]["raw_json"]))

    def test_duplicate_event_with_conflicting_identity_is_rejected(self):
        event, _row = self._event(0, "기사", "충분한 길이의 같은 기사 본문입니다. " * 5)
        common = dict(raw_json="{}", raw_file_uri="file.parquet", kafka_topic="news.raw",
                      kafka_partition=0, published_at=None,
                      collected_at="2026-09-16T01:00:00+00:00")
        first = RawArticle(payload=event, kafka_offset=0, **common)
        conflicting = dict(event)
        conflicting["url"] = "https://example.test/conflict"
        second = RawArticle(payload=conflicting, kafka_offset=1, **common)
        with self.assertRaisesRegex(AnalysisError, "conflicting source/url/content_hash"):
            deduplicate_articles([first, second])

    def test_model_and_analysis_versions_are_nonempty_and_at_most_50(self):
        for overrides in (
            {"model_version": ""},
            {"model_version": "m" * 51},
            {"analysis_version": ""},
            {"analysis_version": "a" * 51},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(AnalysisError):
                self.run_analyzer(**overrides)

    def test_exact_replay_is_noop_and_changed_analysis_conflicts(self):
        first = self.run_analyzer()
        second = self.run_analyzer(analyzed_at="2027-01-01T00:00:00+00:00")
        self.assertEqual(first["output_uri"], second["output_uri"])
        self.assertEqual(second["status"], "already_complete")
        with self.assertRaisesRegex(AnalysisError, "conflicts"):
            self.run_analyzer(analysis_version="different-revision")

    def test_raw_file_hash_mismatch_is_rejected_without_publication(self):
        manifest = json.loads((self.raw / "_manifest.json").read_bytes())
        manifest["files"][0]["sha256"] = "0" * 64
        (self.raw / "_manifest.json").write_bytes(encoded(manifest))
        with self.assertRaisesRegex(AnalysisError, "size or SHA-256 mismatch"):
            self.run_analyzer()
        self.assertFalse(self.output.exists())

    def test_content_hash_mismatch_is_rejected(self):
        parquet_path = self.raw / "region=domestic" / "date=2026-09-16" / "part.parquet"
        rows = pq.read_table(parquet_path).to_pylist()
        payload = json.loads(rows[0]["raw_json"])
        payload["content_hash"] = "f" * 64
        rows[0]["raw_json"] = encoded(payload).decode()
        output = pa.BufferOutputStream()
        pq.write_table(pa.Table.from_pylist(rows), output)
        data = output.getvalue().to_pybytes()
        parquet_path.write_bytes(data)
        manifest = json.loads((self.raw / "_manifest.json").read_bytes())
        manifest["files"][0].update(bytes=len(data), sha256=digest(data))
        (self.raw / "_manifest.json").write_bytes(encoded(manifest))
        with self.assertRaisesRegex(AnalysisError, "content hash mismatch"):
            self.run_analyzer()

    def test_corrupt_completed_output_is_not_accepted_as_replay(self):
        result = self.run_analyzer()
        data_path = Path(result["output_uri"]) / "data.parquet"
        data_path.write_bytes(data_path.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(AnalysisError, "data digest mismatch"):
            self.run_analyzer()


if __name__ == "__main__":
    unittest.main()
