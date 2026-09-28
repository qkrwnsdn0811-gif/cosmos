from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
for path in (HERE, ROOT / "AI" / "graph", ROOT / "AI" / "ner"):
    sys.path.insert(0, str(path))

from finbert_batch import LABEL_SCOPE, canonical_json, enrich_local_batch


def company(stock_code, name, market="KOSPI"):
    return {"market": market, "stock_code": stock_code, "ticker": f"{stock_code}.KS",
            "name": name, "aliases": [name], "confidence": 0.9}


class FakeEngine:
    """Stands in for the models only: the real engine's own rules are exercised
    by EngineRuleTest below, which drives _aggregate with fixed probabilities."""

    provenance = {"model_version": "evidence-sentence-finbert-v1", "test": True}
    provenance_sha256 = hashlib.sha256(canonical_json(provenance)).hexdigest()

    def predict(self, rows):
        from evidence import article_evidence
        results = []
        for row in rows:
            verdicts = []
            for entry in article_evidence(row.get("title") or "", row.get("content") or "",
                                          row.get("companies")):
                analyzed = bool(entry["evidence"])
                verdicts.append({
                    "market": entry["market"], "stock_code": entry["stock_code"],
                    "label": "POSITIVE" if analyzed else None,
                    "score": 0.7 if analyzed else None,
                    "probabilities": [0.1, 0.1, 0.8] if analyzed else None,
                    "status": "analyzed" if analyzed else "no_attributed_sentence",
                    "n_sentences": entry["n_sentences"],
                    "model_role": "sentiment_ko" if analyzed else None,
                    "model_revision": "a" * 40 if analyzed else None,
                    "evidence": [{"sentence_order": item["sentence_order"],
                                  "source": item["source"], "start": item["start"],
                                  "end": item["end"], "text": item["text"],
                                  "language": item["language"], "label": "POSITIVE"}
                                 for item in entry["evidence"]],
                })
            results.append(verdicts)
        return results


def write_source(source: Path, rows: list[dict]) -> None:
    import pyarrow.parquet as pq
    import pyarrow as pa
    pq.write_table(pa.Table.from_pylist(rows), source / "data.parquet")
    data = (source / "data.parquet").read_bytes()
    manifest = {"version": 1, "dataset": "news-historical-company-mentions",
                "analysis_id": "b" * 64,
                "output": {"files": [{"path": "data.parquet", "bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest(),
                                      "records": len(rows)}]}}
    (source / "_manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    (source / "_SUCCESS").write_bytes(b"")


class BatchTest(unittest.TestCase):
    def test_enriches_per_company_and_binds_source_hashes(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            write_source(source, [
                {"article_id": "a", "title": "삼성전자 실적 발표",
                 "content": "삼성전자는 영업이익이 늘었다고 밝혔다. SK하이닉스는 증설을 예고했다.",
                 "language": "ko",
                 "companies": [company("005930", "삼성전자"), company("000660", "SK하이닉스")]},
            ])
            result = enrich_local_batch(source, output, "/input/batch=00000", FakeEngine(),
                                        "2026-09-21T00:00:00+00:00")
            self.assertEqual(result["output"]["status_counts"]["companies"], 2)
            self.assertEqual(result["output"]["status_counts"]["analyzed"], 2)
            enriched = pq.read_table(output / "data.parquet").to_pylist()
            verdicts = {item["stock_code"]: item for item in enriched[0]["ai_company_sentiment"]}
            self.assertEqual(set(verdicts), {"005930", "000660"})
            self.assertEqual(verdicts["005930"]["label"], "POSITIVE")
            self.assertEqual(enriched[0]["ai_sentiment_scope"], LABEL_SCOPE)
            self.assertTrue((output / "_SUCCESS").exists())

    def test_source_row_count_is_unchanged(self):
        """One row per article even though the verdict is per company: the
        manifest's record count must keep describing the same thing."""
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            write_source(source, [
                {"article_id": "a", "title": "제목", "content": "삼성전자는 이익이 늘었다.",
                 "language": "ko", "companies": [company("005930", "삼성전자")]},
                {"article_id": "b", "title": "제목", "content": "본문에 기업이 없다.",
                 "language": "ko", "companies": []},
            ])
            result = enrich_local_batch(source, output, "/input/batch=00001", FakeEngine(),
                                        "2026-09-21T00:00:00+00:00")
            self.assertEqual(result["output"]["records"], 2)
            enriched = pq.read_table(output / "data.parquet").to_pylist()
            self.assertEqual(len(enriched), 2)
            self.assertEqual(enriched[1]["ai_company_sentiment"], [])

    def test_output_reads_back_when_a_company_has_no_verdict(self):
        """A company named only in a shared headline gets no sentence and so a
        NULL label and NULL probabilities. Declaring probabilities as a
        fixed-size list made that row unreadable on the way back out, which the
        loader would hit on the first real article rather than in a test."""
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            write_source(source, [
                {"article_id": "a", "title": "삼성전자, SK하이닉스와 경쟁",
                 "content": "삼성전자는 이익이 늘었다.", "language": "ko",
                 "companies": [company("005930", "삼성전자"), company("000660", "SK하이닉스")]},
            ])
            result = enrich_local_batch(source, output, "/input/batch=00003", FakeEngine(),
                                        "2026-09-21T00:00:00+00:00")
            self.assertEqual(result["output"]["status_counts"]["no_attributed_sentence"], 1)
            verdicts = {item["stock_code"]: item for item
                        in pq.read_table(output / "data.parquet").to_pylist()[0]
                        ["ai_company_sentiment"]}
            self.assertIsNone(verdicts["000660"]["label"])
            self.assertIsNone(verdicts["000660"]["probabilities"])
            self.assertEqual(verdicts["000660"]["evidence"], [])
            self.assertEqual(len(verdicts["005930"]["probabilities"]), 3)

    def test_refuses_a_source_that_is_already_enriched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            write_source(source, [
                {"article_id": "a", "title": "제목", "content": "본문", "language": "ko",
                 "companies": [], "ai_sentiment_scope": "already"},
            ])
            with self.assertRaises(Exception):
                enrich_local_batch(source, output, "/input/batch=00002", FakeEngine(),
                                   "2026-09-21T00:00:00+00:00")


class EngineRuleTest(unittest.TestCase):
    """The aggregation rules, driven directly so no model is loaded."""

    def setUp(self):
        from finbert_batch import FinbertEngine
        self.engine = FinbertEngine.__new__(FinbertEngine)
        self.engine.specs = {"sentiment_ko": {"revision": "k" * 40},
                             "sentiment_en": {"revision": "e" * 40}}

    def aggregate(self, sentences, probabilities):
        entry = {"market": "KOSPI", "stock_code": "005930", "status": "pending",
                 "evidence": [{"sentence_order": index + 1, "source": "body",
                               "start": 0, "end": len(text), "text": text,
                               "language": "ko"}
                              for index, text in enumerate(sentences)]}
        work, index_of = [], {}
        for index, text in enumerate(sentences):
            index_of[(0, "ko", text)] = index
            work.append({"text": text, "language": "ko", "role": "sentiment_ko",
                         "probabilities": probabilities[index]})
        return self.engine._aggregate(entry, 0, work, index_of)

    def test_positive_and_negative_sentences_give_null(self):
        verdict = self.aggregate(["좋다", "나쁘다"], [[0.1, 0.1, 0.8], [0.8, 0.1, 0.1]])
        self.assertIsNone(verdict["label"])
        self.assertEqual(verdict["status"], "conflicting_polarity")

    def test_neutral_with_positive_is_not_a_conflict(self):
        verdict = self.aggregate(["좋다", "그저 그렇다"], [[0.1, 0.1, 0.8], [0.2, 0.7, 0.1]])
        self.assertEqual(verdict["status"], "analyzed")
        self.assertEqual(verdict["label"], "POSITIVE")

    def test_a_firm_neutral_does_not_outvote_weak_directional_sentences(self):
        """Neutral abstains rather than votes.

        FinBERT puts a high neutral probability on most sentences, so averaging
        every sentence together buries the direction: these two barely-positive
        sentences used to lose to the one firmly neutral one. In production
        that showed up as the neutral share *rising* with evidence - 37.3% at
        one sentence, 53.8% at four - which is backwards.
        """
        verdict = self.aggregate(["a", "b", "c"],
                                 [[0.0, 0.45, 0.55], [0.0, 0.45, 0.55], [0.0, 0.95, 0.05]])
        self.assertEqual([item["label"] for item in verdict["evidence"]],
                         ["POSITIVE", "POSITIVE", "NEUTRAL"])
        self.assertEqual(verdict["label"], "POSITIVE")
        self.assertEqual(verdict["status"], "analyzed")
        self.assertEqual(verdict["n_sentences"], 3, "중립도 근거로는 남는다")

    def test_the_neutral_sentence_is_kept_out_of_the_mean(self):
        """The score reports the directional sentences, not a diluted average."""
        verdict = self.aggregate(["a", "b"], [[0.0, 0.40, 0.60], [0.0, 0.90, 0.10]])
        self.assertAlmostEqual(verdict["probabilities"][2], 0.60)
        self.assertAlmostEqual(verdict["score"], 0.60)

    def test_all_neutral_stays_neutral(self):
        """With nothing directional to fall back on, the neutrals are the answer."""
        verdict = self.aggregate(["a", "b"], [[0.1, 0.8, 0.1], [0.2, 0.7, 0.1]])
        self.assertEqual(verdict["label"], "NEUTRAL")
        self.assertEqual(verdict["status"], "analyzed")

    def test_a_negative_among_neutrals_still_decides(self):
        verdict = self.aggregate(["a", "b", "c"],
                                 [[0.0, 0.95, 0.05], [0.0, 0.95, 0.05], [0.7, 0.2, 0.1]])
        self.assertEqual(verdict["label"], "NEGATIVE")

    def test_a_tied_sentence_makes_the_company_null(self):
        verdict = self.aggregate(["a"], [[0.5, 0.0, 0.5]])
        self.assertIsNone(verdict["label"])
        self.assertEqual(verdict["status"], "tied_probabilities")

    def test_repeated_sentence_counts_once(self):
        verdict = self.aggregate(["같은 문장", "같은 문장"],
                                 [[0.1, 0.1, 0.8], [0.1, 0.1, 0.8]])
        self.assertEqual(verdict["n_sentences"], 1)
        self.assertEqual(len(verdict["evidence"]), 1)

    def test_no_evidence_keeps_a_null_label(self):
        entry = {"market": "KOSPI", "stock_code": "005930",
                 "status": "no_attributed_sentence", "evidence": []}
        verdict = self.engine._aggregate(entry, 0, [], {})
        self.assertIsNone(verdict["label"])
        self.assertEqual(verdict["n_sentences"], 0)
        self.assertEqual(verdict["status"], "no_attributed_sentence")


if __name__ == "__main__":
    unittest.main()
