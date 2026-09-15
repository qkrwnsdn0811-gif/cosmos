import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from linker import EntityLinker, normalize  # noqa: E402


class LinkerRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lk = EntityLinker.from_csv(HERE / "data" / "aliases.csv")

    def test_normalize(self):
        self.assertEqual(normalize("(주)삼성전자"), "삼성전자")
        self.assertEqual(normalize("삼성電子"), "삼성전자")
        self.assertEqual(normalize("Samsung Electronics Co., Ltd."), "samsungelectronics")

    def test_exact(self):
        self.assertEqual(self.lk.link("SK하이닉스").ticker, "000660")
        self.assertEqual(self.lk.link("㈜LG화학").ticker, "051910")
        self.assertEqual(self.lk.link("Nvidia").ticker, "NVDA")
        self.assertEqual(self.lk.link("삼전").ticker, "005930")

    def test_hanja_and_fuzzy(self):
        r = self.lk.link("삼성電子")
        self.assertEqual(r.ticker, "005930")
        r = self.lk.link("삼성전자주식회사")
        self.assertEqual(r.ticker, "005930")
        r = self.lk.link("Samsung Electronics Co")
        self.assertEqual(r.ticker, "005930")

    def test_group_default_and_ambiguous(self):
        r = self.lk.link("포스코")
        self.assertEqual((r.ticker, r.method), ("005490", "group_default"))
        r = self.lk.link("삼성")
        self.assertIsNone(r.ticker)
        self.assertEqual(r.reason, "ambiguous_group")

    def test_org_and_sports_filtered(self):
        for s in ("국토교통부", "금융위원회", "민주당", "국회", "원자력안전위원회", "서울 서대문 경찰서"):
            self.assertIsNone(self.lk.link(s).ticker, s)
            self.assertEqual(self.lk.link(s).reason, "org_suffix", s)
        for s in ("삼성 라이온즈", "한화 이글스", "LA 다저스"):
            self.assertEqual(self.lk.link(s).reason, "blocked", s)

    def test_unknown_is_growth_candidate(self):
        for s in ("티웨이항공", "롯데관광개발", "Fortrea Holdings", "노보 노디스크"):
            r = self.lk.link(s)
            self.assertIsNone(r.ticker, s)
            self.assertEqual(r.reason, "unknown", s)

    def test_no_false_fuzzy(self):
        # short or very different strings must not be forced onto a ticker
        for s in ("현대제철", "LG디스플레이", "카카오게임즈", "GM"):
            r = self.lk.link(s)
            self.assertIsNone(r.ticker, f"{s} -> {r}")


    def test_generic_industry_word_never_links(self):
        # difflib scores "Pharmaceutical" against "Hanmi Pharmaceutical" at 0.85
        for s in ("Pharmaceutical", "Technologies", "HealthCare", "Holdings", "전자", "제약", "그룹"):
            r = self.lk.link(s)
            self.assertIsNone(r.ticker, f"{s} -> {r}")

    def test_observed_fuzzy_mislinks_are_rejected(self):
        # every one of these was produced by the 0.84 threshold on real articles
        for s in ("KCIA", "o Innovation", "Opendoor Technologies"):
            r = self.lk.link(s)
            self.assertIsNone(r.ticker, f"{s} -> {r}")

    def test_truncated_surface_still_links(self):
        r = self.lk.link("우리금융지")
        self.assertEqual(r.ticker, "316140")

if __name__ == "__main__":
    unittest.main()
