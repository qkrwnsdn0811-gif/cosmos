import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_sec_edgar_nasdaq100.py"
SPEC = importlib.util.spec_from_file_location("collect_sec_edgar_nasdaq100", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Nasdaq100CollectorTests(unittest.TestCase):
    def test_parses_largest_json_ld_item_list(self):
        items = [
            {"@type": "ListItem", "position": index, "name": f"Company {index}", "description": f"T{index}"}
            for index in range(1, 101)
        ]
        html = (
            '<script type="application/ld+json">'
            + __import__("json").dumps({"@graph": [{"@type": "ItemList", "itemListElement": items}]})
            + "</script>"
        )
        securities = MODULE.parse_nasdaq_securities(html)
        self.assertEqual(100, len(securities))
        self.assertEqual("T1", securities[0].symbol)
        self.assertEqual("Company 100", securities[-1].name)

    def test_parses_current_weighting_payload(self):
        payload = {"aaData": [{"Name": f"Company {index}", "Symbol": f"T{index}"} for index in range(102)]}
        securities = MODULE.parse_nasdaq_weighting_payload(payload)
        self.assertEqual(102, len(securities))
        self.assertEqual("T0", securities[0].symbol)

    def test_parses_global_index_watch_as_of_date(self):
        html = '<div>DATA AS OF <span class="date">9/4/2026</span></div>'
        self.assertEqual("2026-09-04", MODULE.parse_nasdaq_as_of(html))

    def test_universe_collapses_alphabet_share_classes(self):
        securities = [
            MODULE.NasdaqSecurity(1, "ALPHABET CL A CMN", "GOOGL"),
            MODULE.NasdaqSecurity(2, "ALPHABET CL C CAP", "GOOG"),
            MODULE.NasdaqSecurity(3, "MICRON TECHNOLOGY", ""),
        ]
        registry = [
            {"cik": "1652044", "name": "Alphabet Inc.", "ticker": "GOOGL", "exchange": "Nasdaq"},
            {"cik": "1652044", "name": "Alphabet Inc.", "ticker": "GOOG", "exchange": "Nasdaq"},
            {"cik": "723125", "name": "MICRON TECHNOLOGY INC", "ticker": "MU", "exchange": "Nasdaq"},
        ]
        universe = MODULE.build_company_universe(securities, registry, expected_companies=2)
        self.assertEqual(2, len(universe))
        self.assertEqual(["GOOGL", "GOOG"], universe[0]["nasdaq_symbols"])
        self.assertEqual(["MU"], universe[1]["nasdaq_symbols"])

    def test_selects_latest_core_form_and_accepts_amendment(self):
        rows = [
            {"accessionNumber": "1", "filingDate": "2026-09-01", "form": "4"},
            {"accessionNumber": "2", "filingDate": "2026-08-02", "form": "10-Q/A"},
            {"accessionNumber": "3", "filingDate": "2026-08-01", "form": "10-Q"},
        ]
        selected = MODULE.select_filings(rows, {"10-Q"}, None, None, 1)
        self.assertEqual("2", selected[0]["accessionNumber"])

    def test_complete_submission_url_uses_unpadded_cik_and_accession_folder(self):
        url = MODULE.complete_submission_urls("320193", "0000320193-26-000001")[0]
        self.assertEqual(
            "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001.txt",
            url,
        )

    def test_selected_metadata_and_download_use_same_path(self):
        company = {"company_order": 7, "sec_name": "Test Inc", "cik": "123", "nasdaq_symbols": ["AAA", "AAB"]}
        filing = {"accessionNumber": "0000000123-26-000001", "filingDate": "2026-01-02", "form": "10-Q/A"}
        metadata = MODULE.selected_filing_metadata(company, filing)
        self.assertEqual(
            "raw/007_AAA_AAB/2026-01-02_10-Q_A_0000000123-26-000001.txt",
            metadata["localPath"],
        )

    def test_skips_historical_submission_pages_outside_requested_range(self):
        class Client:
            def __init__(self):
                self.requested = []

            def get_json(self, url):
                self.requested.append(url)
                return {"accessionNumber": ["2"], "filingDate": ["2020-01-02"], "form": ["10-K"]}

        submission = {
            "filings": {
                "recent": {"accessionNumber": ["1"], "filingDate": ["2026-01-02"], "form": ["10-K"]},
                "files": [
                    {"name": "old.json", "filingFrom": "2010-01-01", "filingTo": "2015-12-31"},
                    {"name": "in-range.json", "filingFrom": "2016-01-01", "filingTo": "2020-12-31"},
                ],
            }
        }
        client = Client()
        selected = MODULE.load_company_filings(client, submission, {"10-K"}, "2016-01-01", None, 10)
        self.assertEqual(2, len(selected))
        self.assertEqual(1, len(client.requested))
        self.assertTrue(client.requested[0].endswith("in-range.json"))


if __name__ == "__main__":
    unittest.main()
