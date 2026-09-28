import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.dart_documents import parse_document_zip


class DocumentParsingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.zip_path = Path(self.directory.name) / "report.zip"

    def archive(self, entries):
        with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename, content in entries:
                archive.writestr(filename, content)
        return parse_document_zip(self.zip_path)

    def test_uppercase_dart_tags_multi_xml_and_spans(self):
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <DOCUMENT><TITLE>공급계약 체결</TITLE><P>첫 문단입니다.</P><P>둘째 문단입니다.</P>
        <TABLE><THEAD><TR><TH ROWSPAN="2">거래처</TH><TH COLSPAN="2">계약</TH></TR>
        <TR><TH>금액</TH><TH>기간</TH></TR></THEAD><TBODY>
        <TR><TE>테스트 법인</TE><TU>1,000</TU><TD>2026년</TD></TR>
        </TBODY></TABLE></DOCUMENT>'''
        result = self.archive([("report.xml", xml.encode()), ("attachment.XML", b"<DOCUMENT><P>Attachment</P></DOCUMENT>"), ("image.png", b"png")])
        self.assertEqual(result["quality"]["xml_file_count"], 2)
        self.assertEqual(result["quality"]["table_count"], 1)
        self.assertEqual(result["ignored_files"], ["image.png"])
        self.assertIn("첫 문단입니다.\n\n둘째 문단입니다.", result["combined_text"])
        table = result["files"][0]["tables"][0]
        self.assertEqual(table["rows"], [["거래처", "계약", "계약"], ["거래처", "금액", "기간"], ["테스트 법인", "1,000", "2026년"]])
        self.assertEqual(table["cells"][0]["rowspan"], 2)
        self.assertEqual(table["cells"][1]["colspan"], 2)
        for file in result["files"]:
            self.assertEqual(result["combined_text"][file["start_char"]:file["end_char"]], file["text"])

    def test_euc_kr_and_cp949_fallback(self):
        xml = '<?xml version="1.0" encoding="EUC-KR"?><DOCUMENT><P>공급망 계약</P></DOCUMENT>'
        result = self.archive([("legacy.xml", xml.encode("euc-kr"))])
        self.assertEqual(result["files"][0]["text"], "공급망 계약")
        self.assertEqual(result["quality"]["replacement_characters"], 0)
        # CP949's extended Hangul is not representable as EUC-KR.
        xml = '<?xml version="1.0" encoding="EUC-KR"?><P>뷁 공급계약</P>'
        result = self.archive([("cp949.xml", xml.encode("cp949"))])
        self.assertIn("뷁", result["combined_text"])
        self.assertEqual(result["files"][0]["encoding"], "cp949")
        self.assertIn("declared_encoding_fallback", result["files"][0]["warnings"])

    def test_html_entities_line_breaks_and_external_entity_not_expanded(self):
        document = '<!DOCTYPE d [<!ENTITY external SYSTEM "file:///nonexistent/private.txt">]><DOCUMENT><P>A&amp;B&nbsp;계약<BR/>다음 줄 &external;</P><SCRIPT>hidden</SCRIPT></DOCUMENT>'
        result = self.archive([("report.html", document.encode())])
        self.assertIn("A&B 계약\n다음 줄", result["combined_text"])
        self.assertNotIn("hidden", result["combined_text"])
        self.assertNotIn("private.txt", result["combined_text"])

    def test_nested_tables_do_not_add_nested_rows_to_parent(self):
        xml = "<TABLE><TR><TD>outer<TABLE><TR><TD>inner</TD></TR></TABLE></TD></TR></TABLE>"
        result = self.archive([("report.xml", xml)])
        tables = result["files"][0]["tables"]
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]["row_count"], 1)
        self.assertEqual(tables[1]["rows"], [["inner"]])

    def test_corrupt_zip_raises(self):
        self.zip_path.write_bytes(b"not a zip archive")
        with self.assertRaises(zipfile.BadZipFile):
            parse_document_zip(self.zip_path)

if __name__ == "__main__":
    unittest.main()
