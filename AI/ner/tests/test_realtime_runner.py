from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from realtime_runner import (  # noqa: E402
    DEFAULT_OUTPUT_BASE,
    DEFAULT_RAW_GLOB,
    RunnerError,
    discover_pending,
    fair_select,
    output_success_glob,
    raw_key,
    run_once,
    select_with_retry_quota,
    success_key,
)
from realtime_analyzer import MINIMUM_CONFIDENCE, analysis_identity  # noqa: E402


def raw_marker(partition: int, start: int) -> str:
    return (
        "hdfs://100.117.115.44:9000/data-lake/raw/realtime/news/"
        f"topic=news.raw/partition={partition}/start={start:020d}/_manifest.json"
    )


def success_marker(partition: int, start: int) -> str:
    return (
        DEFAULT_OUTPUT_BASE + "/model_version=dict-v1.3/topic=news.raw/"
        f"partition={partition}/start={start:020d}/_SUCCESS"
    )


class RealtimeRunnerTest(unittest.TestCase):
    aliases_digest = "a" * 64
    companies_digest = "b" * 64

    def completed_manifest(self, partition=0, start=0, analysis_version="revision"):
        input_uri = raw_marker(partition, start)[:-len("/_manifest.json")]
        identity, analysis_id = analysis_identity(
            input_uri, "c" * 64, self.aliases_digest, self.companies_digest,
            "dict-v1.3", analysis_version,
        )
        return {
            "version": 1,
            "dataset": "news-company-mentions",
            "schema_version": "news-company-mentions-1.0",
            "analysis_id": analysis_id,
            "identity": identity,
            "model_version": "dict-v1.3",
            "analysis_version": analysis_version,
            "aliases_sha256": self.aliases_digest,
            "companies_sha256": self.companies_digest,
            "minimum_confidence": MINIMUM_CONFIDENCE,
            "input": {"uri": input_uri, "manifest_sha256": "c" * 64,
                      "valid_records": 1},
            "output": {"records": 1, "duplicate_records": 0},
        }

    def test_marker_paths_are_strict(self):
        self.assertEqual(raw_key(raw_marker(2, 14), DEFAULT_RAW_GLOB), ("news.raw", 2, 14))
        self.assertEqual(
            success_key(success_marker(2, 14), DEFAULT_OUTPUT_BASE, "dict-v1.3", "news.raw"),
            ("news.raw", 2, 14),
        )
        with self.assertRaises(RunnerError):
            raw_key(raw_marker(0, 0).replace("start=00000000000000000000", "start=0"), DEFAULT_RAW_GLOB)

    def test_success_marker_is_the_durable_completion_source(self):
        listings = {
            DEFAULT_RAW_GLOB: [raw_marker(0, 0), raw_marker(0, 10), raw_marker(1, 0)],
            output_success_glob(DEFAULT_OUTPUT_BASE, "dict-v1.3", "news.raw"): [success_marker(0, 0)],
        }

        def listed(glob, _binary):
            return listings.get(glob, [])

        pending, stats = discover_pending(
            raw_glob=DEFAULT_RAW_GLOB,
            output_base=DEFAULT_OUTPUT_BASE,
            model_version="dict-v1.3",
            analysis_version="revision",
            aliases_sha256=self.aliases_digest,
            companies_sha256=self.companies_digest,
            hdfs_bin="hdfs",
            list_paths=listed,
            completion_manifest=lambda _marker: self.completed_manifest(),
        )
        self.assertEqual(stats["discovered"], 3)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["pending"], 2)
        self.assertEqual([(p, s) for p, rows in pending.items() for s, _ in rows], [(0, 10), (1, 0)])

    def test_round_robin_prevents_busy_partition_starvation_and_rotates(self):
        pending = {
            0: [(0, raw_marker(0, 0)), (10, raw_marker(0, 10)), (20, raw_marker(0, 20))],
            1: [(0, raw_marker(1, 0)), (10, raw_marker(1, 10))],
            2: [(0, raw_marker(2, 0))],
        }
        selected, cursor = fair_select(pending, 4, None)
        self.assertEqual(
            selected,
            [marker[:-len("/_manifest.json")] for marker in
             (raw_marker(0, 0), raw_marker(1, 0), raw_marker(2, 0), raw_marker(0, 10))],
        )
        self.assertEqual(cursor, 0)
        selected, cursor = fair_select(pending, 2, 0)
        self.assertEqual(
            selected,
            [raw_marker(1, 0)[:-len("/_manifest.json")], raw_marker(2, 0)[:-len("/_manifest.json")]],
        )
        self.assertEqual(cursor, 2)

    def test_completed_marker_with_old_configuration_fails_closed(self):
        listings = {
            DEFAULT_RAW_GLOB: [raw_marker(0, 0)],
            output_success_glob(DEFAULT_OUTPUT_BASE, "dict-v1.3", "news.raw"): [success_marker(0, 0)],
        }
        with self.assertRaisesRegex(RunnerError, "different configuration"):
            discover_pending(
                raw_glob=DEFAULT_RAW_GLOB,
                output_base=DEFAULT_OUTPUT_BASE,
                model_version="dict-v1.3",
                analysis_version="new-revision",
                aliases_sha256=self.aliases_digest,
                companies_sha256=self.companies_digest,
                hdfs_bin="hdfs",
                list_paths=lambda glob, _binary: listings.get(glob, []),
                completion_manifest=lambda _marker: self.completed_manifest(analysis_version="old-revision"),
            )

    def test_failed_oldest_batches_do_not_starve_fresh_batches(self):
        pending = {0: [(start, raw_marker(0, start)) for start in range(6)]}
        failures = {
            raw_marker(0, start)[:-len("/_manifest.json")]: {
                "attempts": 3, "last_attempt_sequence": start,
            }
            for start in range(4)
        }
        selected, _cursor, retried = select_with_retry_quota(
            pending, 4, {"after_partition": None, "failures": failures}
        )
        self.assertIn(raw_marker(0, 4)[:-len("/_manifest.json")], selected)
        self.assertIn(raw_marker(0, 5)[:-len("/_manifest.json")], selected)
        self.assertEqual(retried, 2)

    def test_run_continues_after_one_batch_failure_and_state_is_only_cursor(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.json"
            aliases = Path(temporary) / "aliases.csv"
            companies = Path(temporary) / "companies.csv"
            aliases.write_text("fixture aliases", encoding="utf-8")
            companies.write_text("fixture companies", encoding="utf-8")
            listings = {
                DEFAULT_RAW_GLOB: [raw_marker(0, 0), raw_marker(1, 0)],
                output_success_glob(DEFAULT_OUTPUT_BASE, "dict-v1.3", "news.raw"): [],
            }
            attempted = []

            def listed(glob, _binary):
                return listings.get(glob, [])

            def analyzer(**kwargs):
                attempted.append(kwargs["input_uri"])
                if "partition=0" in kwargs["input_uri"]:
                    raise ValueError("fixture failure")
                return {"status": "published", "output_uri": "hdfs://example/output"}

            result = run_once(
                raw_glob=DEFAULT_RAW_GLOB,
                output_base=DEFAULT_OUTPUT_BASE,
                aliases_path=aliases,
                companies_path=companies,
                model_version="dict-v1.3",
                analysis_version="revision",
                max_batches=10,
                state_file=state,
                hdfs_bin="hdfs",
                list_paths=listed,
                analyzer=analyzer,
            )
            self.assertEqual((result["selected"], result["succeeded"], result["failed"]), (2, 1, 1))
            self.assertEqual(len(attempted), 2)
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_run"], {"selected": 2, "succeeded": 1, "failed": 1})
            self.assertNotIn("completed", saved)
            self.assertEqual(len(saved["failures"]), 1)


if __name__ == "__main__":
    unittest.main()
