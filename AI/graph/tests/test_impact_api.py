"""API 입력 계약: 잘못된 코드/시장/수량 및 임의 추가 필드를 거절한다."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from serve_impact import create_app


class StubRanker:
    model_id = "test-version"
    tickers = ["005930"]
    metadata = {"graph_as_of": "2024-01-01"}

    def rank(self, source, market, top_k):
        if source != "005930":
            raise ValueError("지원하지 않는 종목")
        return {"sourceTicker": source, "targetMarket": market, "companies": [],
                "status": "NO_RELATION_CANDIDATES", "modelVersion": self.model_id}


class APITests(unittest.TestCase):
    def test_contract(self):
        with patch("serve_impact.ImpactRanker", return_value=StubRanker()):
            with TestClient(create_app()) as client:
                self.assertEqual(client.get("/health").json()["modelVersion"], "test-version")
                good = {"sourceTicker": "005930", "targetMarket": "KOSPI"}
                self.assertEqual(client.post("/v1/impact/rank", json=good).status_code, 200)
                for bad in [{}, {**good, "topK": 0}, {**good, "topK": True},
                            {**good, "topK": "5"}, {**good, "targetMarket": "KRX"},
                            {**good, "sourceTicker": "UNKNOWN"}, {**good, "price": 100}]:
                    self.assertEqual(client.post("/v1/impact/rank", json=bad).status_code, 422, bad)

    def test_missing_model_fails_startup(self):
        with patch("serve_impact.ImpactRanker", side_effect=FileNotFoundError("missing model")):
            with self.assertRaises(FileNotFoundError):
                with TestClient(create_app()):
                    pass


if __name__ == "__main__":
    unittest.main()
