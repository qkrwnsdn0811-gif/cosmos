from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from AI.rdb_loader.aggregate import aggregate_records, normalize_feature


AS_OF = datetime(2026, 9, 15, tzinfo=timezone.utc)
A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"


def feature(index=1, *, days=1, score=80, document_type="NEWS", **kwargs):
    return {
        "source_company_id": A, "target_company_id": B, "relationship_type": "SUPPLY",
        "document_id": f"10000000-0000-0000-0000-{index:012d}",
        "document_type": document_type, "score": score,
        "published_at": (AS_OF - timedelta(days=days)).isoformat(), **kwargs,
    }


class AggregateTest(unittest.TestCase):
    def by_window(self, *rows):
        return {r["window_type"]: r for r in aggregate_records(rows, as_of_at=AS_OF)}

    def test_sources_get_equal_weight_despite_document_imbalance(self):
        rows = [feature(1, score=40), feature(2, score=80),
                feature(3, score=100, document_type="DISCLOSURE")]
        for result in self.by_window(*rows).values():
            self.assertEqual(result["news_score"], Decimal(60))
            self.assertEqual(result["disclosure_score"], Decimal(100))
            self.assertEqual(result["score"], Decimal(80))
            self.assertEqual(result["evidence_count"], 3)

    def test_single_source_and_observed_zero_are_not_missing(self):
        for source, absent in [("NEWS", "disclosure_score"), ("DISCLOSURE", "news_score")]:
            with self.subTest(source=source):
                result = self.by_window(feature(score=0, document_type=source))["7D"]
                self.assertIsNone(result[absent])
                self.assertEqual(result["score"], Decimal(0))
                self.assertEqual(result["evidence_count"], 1)

    def test_half_open_windows_include_start_and_exclude_cutoff_and_old_data(self):
        rows = [feature(1, days=7, score=10), feature(2, days=30, score=20),
                feature(3, days=90, score=30), feature(4, days=90.0001, score=100),
                feature(5, days=0, score=100), feature(6, days=-1, score=100)]
        got = self.by_window(*rows)
        self.assertEqual([got[w]["evidence_count"] for w in ["7D", "30D", "90D"]], [1, 2, 3])
        self.assertEqual([got[w]["score"] for w in ["7D", "30D", "90D"]], [10, 15, 20])
        for window, days in [("7D", 7), ("30D", 30), ("90D", 90)]:
            self.assertEqual(got[window]["period_start"], (AS_OF - timedelta(days=days)).isoformat())
            self.assertEqual(got[window]["period_end"], AS_OF.isoformat())

    def test_short_window_without_evidence_stays_all_null(self):
        got = self.by_window(feature(days=20))
        for name in ["news_score", "disclosure_score", "score", "confidence", "impact_direction"]:
            self.assertIsNone(got["7D"][name])
        self.assertEqual(got["7D"]["evidence_count"], 0)
        self.assertEqual(got["30D"]["score"], 80)
        self.assertEqual(aggregate_records([feature(days=91)], as_of_at=AS_OF), [])
        self.assertEqual(aggregate_records([], as_of_at=AS_OF), [])

    def test_same_document_is_counted_once_and_conflicts_rejected(self):
        row = feature()
        self.assertEqual(self.by_window(row, row)["7D"]["evidence_count"], 1)
        for change in [{"score": 79}, {"confidence": 0.3}, {"impact_direction": "POSITIVE"}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
                self.by_window(row, {**row, **change})
        with self.assertRaisesRegex(ValueError, "Conflicting identity"):
            self.by_window(row, {**row, "document_type": "DISCLOSURE"})

    def test_conflicting_document_metadata_across_relationships_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting identity"):
            self.by_window(feature(), feature(relationship_type="INVEST", days=2))

    def test_undirected_endpoint_order_collapses_but_supply_remains_directed(self):
        row = feature(relationship_type="PARTNER")
        reverse = {**row, "source_company_id": B, "target_company_id": A}
        self.assertEqual(self.by_window(row, reverse)["7D"]["evidence_count"], 1)
        supply = {**row, "relationship_type": "SUPPLY"}
        reverse_supply = {**reverse, "relationship_type": "SUPPLY"}
        self.assertEqual(len(aggregate_records([supply, reverse_supply], as_of_at=AS_OF)), 6)

    def test_nullable_confidence_and_direction_aggregation(self):
        rows = [feature(1, confidence=0.4, impact_direction="POSITIVE"),
                feature(2, confidence=0.8, impact_direction="NEGATIVE"), feature(3)]
        got = self.by_window(*rows)["7D"]
        self.assertEqual(got["confidence"], Decimal("0.600000"))
        self.assertIsNone(got["impact_direction"])
        got = self.by_window(rows[0], rows[2])["7D"]
        self.assertEqual(got["impact_direction"], "POSITIVE")

    def test_reproducible_rounding_and_timezone_normalization(self):
        row = feature(score="0.0000015", published_at="2026-09-14T09:00:00+09:00")
        got = self.by_window(row, feature(2, score="0.000001"),
                             feature(3, score="0.000003", document_type="DISCLOSURE"))["7D"]
        self.assertEqual(got["news_score"], Decimal("0.000002"))
        self.assertEqual(got["score"], Decimal("0.000003"))
        self.assertEqual(normalize_feature(row)["published_at"], AS_OF - timedelta(days=1))

    def test_malformed_input_is_rejected(self):
        invalid = [{"score": value} for value in [None, True, -1, 101, "NaN", "Infinity"]]
        invalid += [{"confidence": -0.1}, {"confidence": 1.1}, {"source_company_id": "ticker"},
                    {"target_company_id": A}, {"document_type": "news"},
                    {"published_at": "2026-09-14"}, {"relationship_type": "supply"},
                    {"impact_direction": "UP"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.by_window(feature(**change))
        with self.assertRaisesRegex(ValueError, "timezone"):
            aggregate_records([], as_of_at="2026-09-15")

    def test_reference_output_satisfies_loader_contract_including_null_rows(self):
        from AI.rdb_loader.contract import parse_manifest, validate_row

        rows = aggregate_records([feature(days=20)], as_of_at=AS_OF)
        manifest = parse_manifest({
            "schema_version": 1, "status": "SUCCEEDED", "snapshot_mode": "FULL",
            "snapshot_id": "20000000-0000-0000-0000-000000000001",
            "as_of_at": AS_OF.isoformat(), "formula_version": "evidence-mean-v1",
            "model_version": "fixture-v1", "hdfs_uri": "hdfs://namenode:9000/aggregates/run-1",
            "windows": ["7D", "30D", "90D"], "record_count": 3,
            "window_counts": {"7D": 1, "30D": 1, "90D": 1},
            "files": [{"path": "data/part-00000.parquet", "sha256": "a" * 64}],
        })
        validated = [validate_row(row, manifest) for row in rows]
        self.assertIsNone(validated[0]["score"])
        self.assertEqual(validated[1]["score"], Decimal(80))


if __name__ == "__main__":
    unittest.main()
