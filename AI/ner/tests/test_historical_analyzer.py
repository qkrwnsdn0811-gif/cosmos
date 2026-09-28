import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from historical_analyzer import CONTRACT_VERSION, analyze


class HistoricalAnalyzerTests(unittest.TestCase):
    def test_publishes_bounded_replay_safe_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            rows = []
            for number, title in enumerate(("Apple launches product", "Unrelated market story")):
                body = title + " body"
                rows.append({"schema_version": 1, "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                             "title": title, "content": body,
                             "representative_url": f"https://example.test/story {number}?id={number}",
                             "canonical_url": f"https://example.test/story {number}?id={number}",
                             "source_domain": "example.test", "gdelt_first_seen": 20200102030405,
                             "processed_at": datetime(2026, 9, 20, 1, 2, 3),
                             "archive_collection": "CC-MAIN-2020-05", "archive_timestamp": 20200103000000,
                             "discovery_tickers": ["AAPL"], "discovery_company_ids": ["SEC_0000320193"],
                             "source_urls": [f"https://example.test/story {number}?id={number}"],
                             "run_ids": ["fixture"], "duplicate_rows": 1})
            parquet = source / "part-00000.parquet"
            pq.write_table(pa.Table.from_pylist(rows), parquet)
            stats = {"deduped_articles": "2"}
            (source / "_manifest.json").write_text(json.dumps(stats))
            success = {"run_id": "fixture", "source": "gdelt-commoncrawl", "status": "complete",
                       "parquet_file": parquet.name, "parquet_size": parquet.stat().st_size,
                       "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(), "stats": stats}
            (source / "_SUCCESS.json").write_text(json.dumps(success))
            result = analyze(input_root=str(source), output_base=str(output),
                             aliases_path=Path(__file__).parents[1] / "data" / "aliases.csv",
                             companies_path=Path(__file__).parents[1] / "data" / "companies.csv",
                             model_version="dict-v1.3", analysis_version=CONTRACT_VERSION,
                             batch_rows=1, analyzed_at="2026-09-20T02:00:00Z")
            self.assertEqual(result["processed_rows"], 2)
            self.assertEqual(result["published_batches"], 2)
            first = output / "model_version=dict-v1.3" / "run_id=fixture" / "batch=00000"
            self.assertTrue((first / "_SUCCESS").is_file())
            published = pq.read_table(first / "data.parquet").to_pylist()[0]
            self.assertEqual(published["url"], "https://example.test/story%200?id=0")
            self.assertIn("AAPL", [item["ticker"] for item in published["companies"]])
            replay = analyze(input_root=str(source), output_base=str(output),
                             aliases_path=Path(__file__).parents[1] / "data" / "aliases.csv",
                             companies_path=Path(__file__).parents[1] / "data" / "companies.csv",
                             model_version="dict-v1.3", analysis_version=CONTRACT_VERSION,
                             batch_rows=1, analyzed_at="2026-09-20T02:00:00Z")
            self.assertEqual(replay["already_complete_batches"], 2)


if __name__ == "__main__":
    unittest.main()
