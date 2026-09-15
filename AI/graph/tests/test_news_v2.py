"""Index labels, point-in-time amendments and independent explanation guards."""
import json
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from news_impact_data import Labels, availability, historical_holdings
from news_grounding import parse_claims
from export_historical_ownership import report_snapshot


class IndexTests(unittest.TestCase):
    def test_external_index_not_peer_returns(self):
        dates = pd.bdate_range("2024-01-02", "2024-01-15")
        meta = pd.DataFrame({"ticker": ["A", "B"], "market": ["KOSPI", "KOSPI"]})
        p = pd.DataFrame({"A": np.exp(np.arange(len(dates)) * .02), "B": 100.}, index=dates)
        benchmark = pd.Series(np.exp(np.arange(len(dates)) * .015), index=dates)
        when = availability("2024-01-02", "domestic")
        actual = Labels(p, meta, {"KOSPI": benchmark}).label("A", when, "test")
        self.assertAlmostEqual(actual["y_1d"], .005)
        self.assertAlmostEqual(actual["y_3d"], .015)
        p.B = np.exp(np.arange(len(dates)) * .8)
        self.assertEqual(actual, Labels(p, meta, {"KOSPI": benchmark}).label("A", when, "test"))
        self.assertIsNone(Labels(p, meta, {"KOSPI": benchmark.drop(dates[3])}).label("A", when, "test"))

    def test_amendment_never_time_travels_or_replaces_newer_period(self):
        def row(period, published, report, parsed=True):
            return dict(filer="A", report_name=f"사업보고서 ({period})", published_date=published,
                        report_id=report, parsed=parsed)
        history = [row("2021.12", "2022-03-01", "a"), row("2022.12", "2023-03-01", "b"),
                   row("2021.12", "2023-06-01", "c"), row("2023.12", "2024-03-01", "d", False)]
        self.assertEqual(historical_holdings(history, "2023-01-01")["A"]["report_id"], "a")
        self.assertEqual(historical_holdings(history, "2024-01-01")["A"]["report_id"], "b")
        self.assertFalse(historical_holdings(history, "2025-01-01")["A"]["parsed"])

    def test_bad_report_dates_are_not_accepted(self):
        r = dict(rcept_no="20230301000001", rcept_dt="20220301", report_nm="사업보고서 (2021.12)")
        self.assertIsNone(report_snapshot(r, "A", {}))


class GroundingTests(unittest.TestCase):
    def test_single_source_numbers_and_schema(self):
        evidence = [{"id": "E1", "kind": "PARTNER", "text": "2021년 삼성전자와 한국전력이 협약을 체결했다."}]
        c = {"evidence_id": "E1", "quote": evidence[0]["text"], "summary": "삼성전자와 한국전력은 협약을 맺었다."}
        self.assertEqual(len(parse_claims(json.dumps({"claims": [c]}), evidence)), 1)
        for bad in [{**c, "summary": "2024년 삼성전자와 한국전력이 협약을 맺었다."},
                    {**c, "quote": "2024년 삼성전자와 한국전력이 협약을 체결했다."},
                    {**c, "impact_score": .9}, {**c, "evidence_id": "E2"}]:
            with self.assertRaises((ValueError, KeyError)):
                parse_claims(json.dumps({"claims": [bad]}), evidence)

    def test_correlation_cannot_be_paraphrased_into_contract(self):
        evidence = [{"id": "E1", "kind": "correlation", "text": "A/B prior-year price correlation 0.8"}]
        c = {"evidence_id": "E1", "quote": evidence[0]["text"], "summary": "A 기업이 B 기업에 제품을 공급한다."}
        with self.assertRaises(ValueError):
            parse_claims(json.dumps({"claims": [c]}), evidence)

    def test_agreement_and_plan_are_not_execution(self):
        for quote, summary in [("삼성전자와 한국전력은 개발 협약을 체결했다.", "삼성전자와 한국전력은 개발을 진행 중이다."),
                               ("삼성전자는 설비 투자를 확대할 계획이라고 밝혔다.", "삼성전자는 설비 투자를 확대했다.")]:
            evidence = [{"id": "E1", "kind": "PARTNER", "text": quote}]
            c = dict(evidence_id="E1", quote=quote, summary=summary)
            with self.assertRaises(ValueError):
                parse_claims(json.dumps({"claims": [c]}), evidence)


if __name__ == "__main__":
    unittest.main()
