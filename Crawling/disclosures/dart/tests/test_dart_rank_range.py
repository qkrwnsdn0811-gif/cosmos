"""Offline integration tests for rank collection, durable resume, and quotas."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from scripts import collect_dart_rank_range as collect


class RankCollectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "output"
        self.company = {"universe_rank": 51, "stock_code": "000051", "corp_code": "00000051", "corp_name": "시험 법인"}

    def api(self, key="k" * 40, **kwargs):
        return collect.Api(key, self.output, interval=0, ledger_dir=self.root / "ledger", **kwargs)

    def row(self, receipt="20260101000001", title="단일판매·공급계약체결"):
        return {**self.company, "rcept_no": receipt, "rcept_dt": receipt[:8], "report_nm": title, "flr_nm": "시험 법인"}

    def zip_bytes(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.xml", "<DOCUMENT><P>기업 간 공급계약 체결</P></DOCUMENT>".encode())
        return stream.getvalue()

    def args(self, plan_only=False):
        return argparse.Namespace(rank_from=51, rank_to=51, begin="20260101", end="20261231",
                                  universe=self.root / "universe.jsonl", workers=4, interval=0,
                                  daily_limit=19500, plan_only=plan_only, reuse_source=[])

    def test_only_two_types_excluded_with_punctuation_and_wrappers(self):
        for title in ["임원ㆍ주요주주특정증권등소유상황보고서", "[기재정정] 임원 · 주요주주 특정증권등 소유상황보고서 (일반)",
                      "【첨부정정】임원・주요주주특정증권등거래계획보고서（정정）"]:
            self.assertIn(collect.exclusion_code({"report_nm": title}), {"D002", "D005"})
        for title in ["[기재정정]사업보고서", "주식등의대량보유상황보고서(일반)", "대규모기업집단현황공시", "임원·주요주주특정증권등소유상황보고서에대한안내"]:
            self.assertIsNone(collect.exclusion_code({"report_nm": title}))

    def test_thread_budget_and_key_specific_daily_resume(self):
        api = self.api(max_new_requests=3, initial_used=1, daily_limit=6)
        def reserve(number):
            try:
                api.reserve("document.xml", {"rcept_no": str(number)})
                return True
            except collect.RequestStopped:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(30))), 3)
        self.assertEqual(api.daily_used, 4)
        resumed = self.api(initial_used=1, daily_limit=6)
        resumed.reserve("document.xml", {})
        resumed.reserve("document.xml", {})
        with self.assertRaises(collect.RequestStopped):
            resumed.reserve("document.xml", {})
        self.assertEqual(resumed.used, 2)
        self.assertEqual(self.api(key="j" * 40).daily_used, 0)
        self.assertNotIn("k" * 40, api.ledger_path.read_text())

    def test_every_service_stop_status_stops_all_workers(self):
        for status in collect.STOP_STATUSES:
            api = self.api()
            with self.assertRaises(collect.RequestStopped):
                api.check_status(status)
            with self.assertRaises(collect.RequestStopped):
                api.reserve("list.json", {})
            self.assertEqual(api.used, 0)

    def test_network_error_does_not_reveal_key_or_url(self):
        api = self.api()
        secret_url = collect.BASE_URL + "list.json?crtfc_key=" + "k" * 40
        with patch.object(collect.urllib.request, "urlopen", side_effect=urllib.error.URLError(secret_url)), patch.object(collect.time, "sleep"):
            with self.assertRaises(collect.CollectorError) as error:
                api.request("list.json", {})
        self.assertEqual(str(error.exception), "network_error_URLError")
        self.assertEqual(api.used, 3)

    def test_output_lock_is_exclusive_and_released_on_exception(self):
        lock_path = self.output / ".collector.lock"
        with self.assertRaisesRegex(ValueError, "interrupted"):
            with collect.FileLock(lock_path):
                with self.assertRaises(collect.CollectorError):
                    with collect.FileLock(lock_path):
                        pass
                raise ValueError("interrupted")
        with collect.FileLock(lock_path):
            pass

    def test_complete_prior_list_requests_only_missing_head_and_tail(self):
        source = self.root / "source"
        collect.write_json(source / "metadata/company_lists/000051.json", {
            "begin": "20160101", "end": "20260907", "complete": True,
            "rows": [self.row(), self.row("20150101000001")],
        })
        api = self.api()
        requested = []
        def request(endpoint, params):
            requested.append(params)
            return json.dumps({"status": "013"}).encode()
        with patch.object(api, "request", side_effect=request):
            result = collect.collect_company_list(api, self.company, "19990101", "20260909", [source])
        self.assertTrue(result["complete"])
        self.assertEqual([(p["bgn_de"], p["end_de"]) for p in requested], [("19990101", "20151231"), ("20260908", "20260909")])
        self.assertTrue(all(p["page_count"] == 100 and p["last_reprt_at"] == "N" for p in requested))
        # A reused collection is filtered to its own validated covered period.
        self.assertEqual(len(result["rows"]), 1)
        with patch.object(api, "request", side_effect=AssertionError("complete list must resume offline")):
            resumed = collect.collect_company_list(api, self.company, "19990101", "20260909", [source])
        self.assertEqual(resumed["rows"], result["rows"])

    def test_interrupted_pagination_reuses_raw_response_and_deduplicates(self):
        api = self.api()
        first = {"status": "000", "total_page": 2, "list": [self.row()]}
        with patch.object(api, "request", side_effect=[json.dumps(first).encode(), collect.RequestStopped("budget")]):
            incomplete = collect.collect_company_list(api, self.company, "20260101", "20261231", [])
        self.assertFalse(incomplete["complete"])
        second = {"status": "000", "total_page": 2, "list": [self.row(), self.row("20260201000001", "[기재정정]사업보고서")]}
        with patch.object(api, "request", return_value=json.dumps(second).encode()) as request:
            completed = collect.collect_company_list(api, self.company, "20260101", "20261231", [])
        self.assertTrue(completed["complete"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1]["page_no"], 2)
        self.assertEqual(len(completed["rows"]), 2)

    def test_unavailable_response_persists_and_avoids_repeat_request(self):
        api = self.api()
        for status, receipt in [("013", "20260101000001"), ("014", "20260101000002")]:
            with patch.object(api, "request", return_value=f"<result><status>{status}</status></result>".encode()) as request:
                result = collect.document_one(api, self.row(receipt), {})
                resumed = collect.document_one(api, self.row(receipt), {})
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(resumed["api_status"], status)
            self.assertEqual(request.call_count, 1)

    def test_empty_or_whitespace_xml_is_not_a_collected_document(self):
        for index, xml in enumerate(("<DOCUMENT/>", "<DOCUMENT><P> \n\t&#160; </P></DOCUMENT>")):
            with self.subTest(xml=xml):
                receipt = f"2026010100000{index + 1}"
                content = io.BytesIO()
                with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("report.xml", xml)
                original = content.getvalue()
                api = self.api()
                with patch.object(api, "request", return_value=original):
                    result = collect.document_one(api, self.row(receipt), {})
                self.assertEqual((result["status"], result["error"]), ("parse_error", "empty_document_text"))
                self.assertNotIn("detail", result)
                self.assertEqual((self.output / "raw/documents" / (receipt + ".zip")).read_bytes(), original)
                stored = collect.read_json(self.output / "state/documents" / (receipt + ".json"))
                self.assertEqual(stored["error"], "empty_document_text")

    def test_empty_attachment_does_not_discard_other_valid_text(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("empty.xml", "<DOCUMENT/>")
            archive.writestr("valid.xml", "<DOCUMENT><P>Valid body</P></DOCUMENT>")
        api = self.api()
        with patch.object(api, "request", return_value=content.getvalue()):
            result = collect.document_one(api, self.row(), {})
        self.assertEqual(result["status"], "collected")
        self.assertEqual([item["text"] for item in result["detail"]["files"]], ["", "Valid body"])

    def test_original_reuse_exports_attached_text_and_resume_has_no_duplicates(self):
        source = self.root / "source"
        raw = source / "raw/documents/000051_20260101000001_2025_annual.zip"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(self.zip_bytes())
        api = self.api(max_new_requests=0)
        args = self.args()
        args.reuse_source = [source]
        collect.write_json(self.output / "metadata/company_lists/000051.json", {
            "begin": args.begin, "end": args.end, "complete": True,
            "rows": [self.row(), self.row("20260102000001", "임원ㆍ주요주주특정증권등소유상황보고서")],
        })
        with patch.object(api, "request", side_effect=AssertionError("all originals are local")):
            result = collect.collect(api, args, [self.company])
            resumed = collect.collect(api, args, [self.company])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(resumed["totals"]["collected"], 1)
        self.assertEqual(resumed["totals"]["excluded"], 1)
        lines = (self.output / "companies" / (collect.company_stem(self.company) + "_detail.jsonl")).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        detail = json.loads(lines[0])
        self.assertEqual(detail["files"], [{"filename": "report.xml", "text": "기업 간 공급계약 체결"}])
        self.assertEqual((self.output / detail["raw_zip"]).read_bytes(), raw.read_bytes())

    def test_partial_jsonl_tail_is_removed_and_valid_lines_preserved(self):
        path = self.root / "detail.jsonl"
        good = json.dumps(self.row()).encode() + b"\n"
        path.write_bytes(good + b'{"rcept_no": "202')
        self.assertEqual(collect.load_detail_receipts(path), {"20260101000001"})
        self.assertEqual(path.read_bytes(), good)

    def test_plan_only_never_requests_documents(self):
        api = self.api()
        args = self.args(plan_only=True)
        collect.write_json(self.output / "metadata/company_lists/000051.json", {
            "begin": args.begin, "end": args.end, "complete": True, "rows": [self.row()],
        })
        with patch.object(api, "request", side_effect=AssertionError("plan requires no documents")):
            result = collect.collect(api, args, [self.company])
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["totals"]["not_collected"], 1)

    def test_atomic_replace_retries_transient_permission_failure(self):
        target = self.root / "manifest.json"
        target.write_text('{"status":"old"}', encoding="utf-8")
        real_replace = collect.os.replace
        attempts = []

        def replace(source, destination):
            attempts.append((source, destination))
            if len(attempts) <= 2:
                self.assertEqual(json.loads(target.read_text())["status"], "old")
                raise PermissionError("temporary sharing violation")
            return real_replace(source, destination)

        with patch.object(collect.os, "replace", side_effect=replace), patch.object(collect.time, "sleep") as sleep:
            collect.write_json(target, {"status": "new"})
        self.assertEqual(json.loads(target.read_text())["status"], "new")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])
        self.assertFalse(target.with_name(target.name + ".tmp").exists())

    def test_atomic_replace_permanent_permission_failure_is_bounded(self):
        target = self.root / "manifest.json"
        target.write_text('{"status":"old"}', encoding="utf-8")
        failure = PermissionError("permanent access denied")
        with patch.object(collect.os, "replace", side_effect=failure) as replace, patch.object(collect.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                collect.write_json(target, {"status": "new"})
        self.assertIs(caught.exception, failure)
        self.assertEqual(replace.call_count, 6)
        delays = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(delays, [0.05, 0.1, 0.2, 0.4, 0.8])
        self.assertLess(sum(delays), 2)
        self.assertEqual(json.loads(target.read_text())["status"], "old")


if __name__ == "__main__":
    unittest.main()
