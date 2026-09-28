"""Window boundaries, duplicate evidence, source isolation and generated loader contract."""
import csv
import datetime as dt
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from relationship_score_components import (aggregate, blend, component, normalize,
    ownership_evidence, json_rows, timestamp, SCORE_FIELDS)


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.at = timestamp("2026-09-15")
        self.markets = {"A": "KOSPI", "B": "NASDAQ"}

    def hit(self, date="2026-09-14", document="news1", confidence=1.0,
            source="NEWS", src="A", dst="B", rel="PARTNER"):
        return normalize({"src_ticker": src, "dst_ticker": dst, "rel_type": rel,
            "record_id": document, "published_date": date, "confidence": confidence}, source)

    def scores(self, items):
        return aggregate(items, self.markets, self.at)[0]

    def test_window_boundaries_and_future_exclusion(self):
        for days in (7, 30, 90):
            start = self.at - dt.timedelta(days=days)
            rows = self.scores([self.hit(start.isoformat())])
            windows = {r["window_type"] for r in rows}
            self.assertEqual(windows, {f"{n}D" for n in (7, 30, 90) if n >= days})
            rows = self.scores([self.hit((start-dt.timedelta(microseconds=1)).isoformat())])
            self.assertNotIn(f"{days}D", {r["window_type"] for r in rows})
        self.assertEqual(self.scores([self.hit(self.at.isoformat())]), [])
        self.assertEqual(self.scores([self.hit("2026-09-16")]), [])

    def test_date_only_is_end_of_date_and_offsets_agree(self):
        self.assertEqual(timestamp("2026-09-14", evidence=True), self.at-dt.timedelta(microseconds=1))
        self.assertEqual(timestamp("2026-09-15T09:00:00+09:00"), self.at)
        with self.assertRaises(ValueError):
            timestamp("2026-09-15T09:00:00")

    def test_document_dedup_uses_max_confidence_and_is_order_independent(self):
        items = [self.hit(confidence=.3), self.hit(confidence=.8), self.hit(confidence=.5)]
        rows = self.scores(items)
        self.assertEqual(rows, self.scores(list(reversed(items))))
        self.assertEqual(rows, self.scores([self.hit(confidence=.8)]))
        self.assertEqual(rows[0]["evidence_count"], 1)

    def test_sources_are_separate_even_with_same_document_id(self):
        row = self.scores([self.hit(confidence=.5), self.hit(confidence=1, source="DISCLOSURE")])[0]
        self.assertEqual(row["evidence_count"], 2)
        self.assertEqual(row["score"], round((row["news_score"]+row["disclosure_score"])/2, 6))
        self.assertNotEqual(row["news_score"], row["disclosure_score"])

    def test_null_fallback_and_observed_zero(self):
        self.assertIsNone(blend(None, None))
        self.assertEqual(blend(30, None), 30)
        self.assertEqual(blend(None, 40), 40)
        self.assertEqual(blend(0, 40), 20)
        row = self.scores([self.hit(confidence=0)])[0]
        self.assertEqual(row["news_score"], 0)
        self.assertIsNone(row["disclosure_score"])
        self.assertEqual(row["score"], 0)
        self.assertEqual(self.scores([]), [])

    def test_direction_and_type_preserved(self):
        items = [self.hit(), self.hit(src="B", dst="A")]
        self.assertEqual(self.scores(items)[0]["evidence_count"], 1)
        items = [self.hit(rel="SUPPLY"), self.hit(src="B", dst="A", rel="SUPPLY"),
                 self.hit(rel="INVEST", source="DISCLOSURE")]
        self.assertEqual(len(self.scores(items)), 9)

    def test_same_document_conflicting_dates_rejected(self):
        with self.assertRaises(ValueError):
            self.scores([self.hit(), self.hit(date="2026-09-13")])

    def test_bad_confidences_rejected_and_unknown_companies_reported(self):
        for value in (-.1, 1.1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.hit(confidence=value)
        rows, unknown = aggregate([self.hit(dst="UNKNOWN")], self.markets, self.at)
        self.assertEqual(rows, [])
        self.assertEqual(unknown, [("A", "UNKNOWN", "PARTNER")])

    def test_same_support_formula_for_both_sources(self):
        expected = round(100*(1-math.exp(-1)), 6)
        for source in ("NEWS", "DISCLOSURE"):
            docs = [self.hit(document=str(i), source=source) for i in range(5)]
            self.assertEqual(component(docs), expected)
        self.assertLessEqual(component([self.hit()]*10000), 100)

    def test_ownership_uses_receipt_date_not_accounting_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"ownership.csv"
            path.write_text("src_ticker,dst_ticker,as_of_date,evidence\nA,B,2025-12-31,dart:20260914000217;pct=70\n", encoding="utf-8")
            items = [normalize(r, "DISCLOSURE") for r in ownership_evidence(path)]
            self.assertEqual(len(self.scores(items)), 3)
            self.assertEqual(items[0]["confidence"], 1)
            path.write_text("src_ticker,dst_ticker,as_of_date,evidence\nA,B,2026-09-14,missing\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                list(ownership_evidence(path))

    def test_export_completion_and_missing_input_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with self.assertRaises(ValueError):
                list(json_rows(path))
            (path/"_SUCCESS").touch()
            self.assertEqual(list(json_rows(path)), [])
            (path/"part-0000.json").write_text('{"record_id":"one"}\n', encoding="utf-8")
            self.assertEqual(list(json_rows(path)), [{"record_id":"one"}])
            with self.assertRaises(FileNotFoundError):
                list(json_rows(path/"missing.jsonl"))

    def test_cli_generates_nullable_columns_all_windows_and_new_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            news = path/"news.jsonl"
            news.write_text(json.dumps({"src_ticker":"A","dst_ticker":"B","rel_type":"PARTNER",
                "record_id":"one","published_date":"2026-09-14","confidence":.8})+'\n', encoding="utf-8")
            disclosure = path/"disclosure.jsonl"
            disclosure.write_text('', encoding="utf-8")
            companies = path/"companies.csv"
            companies.write_text("ticker,market\nA,KOSPI\nB,NASDAQ\n", encoding="utf-8")
            out = path/"out"
            command = [sys.executable, str(HERE/"build_relationship_seed.py"), "--components",
                "--news-hits", str(news), "--disclosure-evidence", str(disclosure),
                "--companies", str(companies), "--as-of", "2026-09-15", "--out", str(out)]
            subprocess.run(command, check=True, capture_output=True)
            with (out/"relationship_score_current.csv").open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, SCORE_FIELDS)
            self.assertEqual({r["window_type"] for r in rows}, {"7D","30D","90D"})
            for row in rows:
                self.assertEqual(row["disclosure_score"], '')
                self.assertEqual(row["score"], row["news_score"])
            self.assertEqual((out/"load_relationships.sql").read_text(encoding="utf-8"),
                             (HERE/"load_relationship_components.sql").read_text(encoding="utf-8"))
            # A completed empty batch must still publish a snapshot with header-only scores.
            news.write_text('', encoding="utf-8")
            subprocess.run(command, check=True, capture_output=True)
            with (out/"relationship_score_current.csv").open(encoding="utf-8") as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])


if __name__ == "__main__":
    unittest.main()
