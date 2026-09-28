from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
for path in (HERE, ROOT / "AI" / "graph", ROOT / "AI" / "ner"):
    sys.path.insert(0, str(path))

from relations import article_relations


def company(stock_code, name, *aliases, market="KOSPI"):
    return {"market": market, "stock_code": stock_code, "ticker": f"{stock_code}.KS",
            "name": name, "aliases": list(aliases), "confidence": 1.0, "method": "dict"}


KR = [company("005930", "삼성전자"), company("000660", "SK하이닉스")]


class ExtractionTest(unittest.TestCase):
    def test_a_two_company_sentence_becomes_a_relation(self):
        result = article_relations(
            "반도체 공급망 재편",
            "삼성전자가 SK하이닉스와 협력을 강화하기로 했다.", KR, "ko")
        self.assertTrue(result)
        self.assertEqual(result[0]["relation_type"], "PARTNER")
        self.assertIn("협력", result[0]["text"])

    def test_the_sentence_sentiment_keeps_is_not_a_relation(self):
        """One company means no pair, whatever the predicate says."""
        result = article_relations(
            "실적 발표", "삼성전자는 영업이익이 늘었다고 밝혔다.",
            [company("005930", "삼성전자")], "ko")
        self.assertEqual(result, [])

    def test_an_article_with_one_company_yields_nothing(self):
        self.assertEqual(article_relations("제목", "삼성전자는 이익이 늘었다.",
                                           [company("005930", "삼성전자")], "ko"), [])

    def test_the_evidence_sentence_is_carried_with_the_relation(self):
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 기술 협력을 확대한다.", KR, "ko")
        self.assertTrue(result)
        row = result[0]
        self.assertIn("SK하이닉스", row["text"])
        self.assertIn("삼성전자", row["text"])
        self.assertGreaterEqual(row["sentence_order"], 1)
        self.assertIn(row["source"], ("title", "body"))

    def test_enumeration_is_reported_so_the_caller_can_drop_it(self):
        """'A·B·C 와 협약' names three parties that contracted with a fourth,
        not with each other. The classifier lets it through for PARTNER, so the
        flag is how a caller filters it."""
        result = article_relations(
            "지자체 협약",
            "강원도가 삼성전자, SK하이닉스와 손잡고 지원에 나섰다.", KR, "ko")
        if result:
            self.assertTrue(any(row["enumeration"] for row in result))

    def test_one_sentence_yields_one_row_per_pair(self):
        """Both orders of a pair are asked; a symmetric answer must not double."""
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력한다.", KR, "ko")
        keys = [(r["relation_type"], r["source_stock_code"],
                 r["target_stock_code"], r["sentence_order"]) for r in result]
        self.assertEqual(len(keys), len(set(keys)))

    def test_pairs_carry_market_and_code_for_both_sides(self):
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력한다.", KR, "ko")
        self.assertTrue(result)
        row = result[0]
        for field in ("source_market", "source_stock_code",
                      "target_market", "target_stock_code"):
            self.assertTrue(row[field])
        self.assertNotEqual(row["source_stock_code"], row["target_stock_code"])


class EnglishTest(unittest.TestCase):
    US = [company("AAPL", "Apple", market="NASDAQ"),
          company("INTC", "Intel", market="NASDAQ")]

    def test_english_sentences_use_the_english_classifier(self):
        result = article_relations(
            "Chip market", "Apple competes with Intel in the laptop market.",
            self.US, "en")
        for row in result:
            self.assertEqual(row["language"], "en")
            self.assertTrue(row["pattern"].startswith("en:"))

    def test_boilerplate_and_captions_never_reach_the_classifier(self):
        result = article_relations("Results", "[사진=Apple] Intel", self.US, "en")
        self.assertEqual(result, [])


class SubstringNameTest(unittest.TestCase):
    """A short name matching inside a longer one invents pairs that the
    sentence never states."""

    GROUP = [company("035720", "카카오"), company("377300", "카카오페이"),
             company("323410", "카카오뱅크")]

    def test_a_name_found_only_inside_a_longer_one_is_not_a_party(self):
        result = article_relations(
            "제휴", "카카오페이와 카카오뱅크는 파이어블록스와 손잡고 사업을 검토한다.",
            self.GROUP, "ko")
        involved = {code for row in result
                    for code in (row["source_stock_code"], row["target_stock_code"])}
        self.assertNotIn("035720", involved)

    def test_the_longer_company_still_takes_part(self):
        result = article_relations(
            "제휴", "카카오페이와 카카오뱅크는 협력 구조를 검토한다.", self.GROUP, "ko")
        involved = {code for row in result
                    for code in (row["source_stock_code"], row["target_stock_code"])}
        self.assertTrue({"377300", "323410"} & involved)

    def test_the_short_name_counts_when_it_stands_alone(self):
        result = article_relations(
            "제휴", "카카오가 카카오뱅크와 별도로 협력 방안을 논의했다.",
            [company("035720", "카카오"), company("323410", "카카오뱅크")], "ko")
        involved = {code for row in result
                    for code in (row["source_stock_code"], row["target_stock_code"])}
        self.assertIn("035720", involved)


class MatchConfidenceTest(unittest.TestCase):
    """Only identities the matcher was certain about can carry a relation."""

    def test_a_group_name_guess_is_not_a_party(self):
        weak = dict(company("005930", "삼성전자"), confidence=0.5, method="rule")
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력한다.",
            [weak, company("000660", "SK하이닉스")], "ko")
        self.assertEqual(result, [])

    def test_certain_hits_still_produce_relations(self):
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력한다.",
            [dict(company("005930", "삼성전자"), method="dict"),
             dict(company("000660", "SK하이닉스"), method="dict")], "ko")
        self.assertTrue(result)

    def test_an_ellipsis_expansion_is_flagged_like_an_enumeration(self):
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력한다.",
            [dict(company("005930", "삼성전자"), method="dict+ellipsis"),
             dict(company("000660", "SK하이닉스"), method="dict")], "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["enumeration"] for row in result))


class PrecisionRuleTest(unittest.TestCase):
    """Rows the first live pass (2026-09-23) got wrong, with the reason each now carries.

    The classifier is validated on two-company sentences and never looks past the
    pair; these are the shapes that slipped through the realtime path.
    """

    KAKAO = [company("035720", "카카오"), company("377300", "카카오페이"),
             company("323410", "카카오뱅크")]

    def test_a_sentence_naming_three_companies_is_crowded(self):
        """'카카오·카카오뱅크·카카오페이 등 카카오그룹은 … 파이어블록스와 협약' - a
        roster, and the counterparty is outside it. Read as 카카오↔카카오페이 PARTNER."""
        result = article_relations(
            "협약", "카카오·카카오뱅크·카카오페이 등 카카오그룹은 전날 글로벌 블록체인 "
                    "인프라 기업 파이어블록스와 협약을 체결했다.", self.KAKAO, "ko")
        self.assertTrue(result, "분류기는 여전히 후보를 내야 한다 - 버리는 건 이유와 함께")
        self.assertTrue(all(row["reject"] == "crowded" for row in result))
        self.assertTrue(all(row["enumeration"] for row in result))

    def test_a_survey_roster_is_crowded(self):
        """'삼성·SK·현대자동차·LG·…·HD현대 등 조사에 응한' with 협력 in 협력센터."""
        cos = [company("005380", "현대차", "현대자동차"), company("267250", "HD현대"),
               company("005930", "삼성전자")]
        result = article_relations(
            "조사", "한경협중소기업협력센터에 따르면 삼성전자·현대자동차·HD현대 등 조사에 "
                    "응한 기업의 70%가 상생 협력을 늘렸다.", cos, "ko")
        self.assertTrue(all(row["reject"] == "crowded" for row in result))

    def test_two_companies_in_a_typographic_list_closed_by_등_are_co_listed(self):
        result = article_relations(
            "협약", "카카오·카카오페이 등은 파이어블록스와 협약을 체결했다.",
            self.KAKAO[:2], "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] in ("co_listed", "enumeration") for row in result))
        self.assertTrue(all(row["enumeration"] for row in result))

    def test_unlike_is_not_a_partnership(self):
        """'쇼피파이는 아마존과 달리 …' came back PARTNER from the first pass."""
        cos = [company("SHOP", "Shopify", "쇼피파이", market="NASDAQ"),
               company("AMZN", "Amazon", "아마존", market="NASDAQ")]
        result = article_relations(
            "전략", "쇼피파이는 아마존과 달리 판매자와 협력하는 모델을 택했다.", cos, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "contrast" for row in result))
        self.assertFalse(any(row["enumeration"] for row in result), "대비는 나열이 아니다")

    def test_a_plain_two_company_partnership_carries_no_reason(self):
        result = article_relations(
            "제목", "삼성전자가 SK하이닉스와 협력을 강화하기로 했다.", KR, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] is None for row in result))

    def test_a_pair_joined_by_와_is_the_canonical_statement_and_passes(self):
        """'한화에어로스페이스와 KAI는 … MOU를 체결' - enum+kw with a grammatical
        conjunction. The old blanket flag threw these away: 3 of 4 were correct."""
        cos = [company("012450", "한화에어로스페이스"), company("047810", "한국항공우주", "KAI")]
        result = article_relations(
            "협약", "한화에어로스페이스와 KAI는 장거리 공대공 유도탄 체계개발 사업 성공을 "
                    "위한 상호협력 양해각서(MOU)를 체결했다고 23일 밝혔다.", cos, "ko")
        self.assertTrue(result)
        self.assertEqual(result[0]["pattern"], "enum+kw")
        self.assertIsNone(result[0]["reject"])
        self.assertEqual(result[0]["confidence"], 0.8)

    def test_a_pair_joined_by_a_comma_is_a_roster(self):
        """'Npay 비상장은 한국투자증권, 키움증권과 … 각각 체결' - both contracted
        with Npay, not with each other."""
        cos = [company("071050", "한국금융지주", "한국투자증권"), company("039490", "키움증권")]
        result = article_relations(
            "협약", "Npay 비상장은 한국투자증권, 키움증권과 비상장주식 장외거래 시장 활성화를 "
                    "위한 업무협약(MOU)을 각각 체결했다.", cos, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "co_listed" for row in result))

    def test_a_pair_that_contracts_with_a_third_name_is_not_a_pair(self):
        """'카카오페이와 카카오뱅크가 파이어블록스와 업무협약(MOU)을 체결' - both signed
        with 파이어블록스, which is outside the universe and right after the pair."""
        result = article_relations(
            "협약", "카카오페이와 카카오뱅크가 파이어블록스와 업무협약(MOU)을 체결하고 국내 "
                    "디지털자산 인프라 구축에 나선다.", self.KAKAO[1:], "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "third_party" for row in result))

    def test_등_followed_by_a_word_for_companies_is_a_roster(self):
        """'삼성전자와 SK하이닉스 등 국내 기업들에 악재' - 국내 sits between 등 and 기업,
        past the classifier's own roster rule."""
        result = article_relations(
            "분석", "중국 메모리 업체의 추격이 가속화하며 삼성전자와 SK하이닉스 등 국내 기업들에 "
                    "악재로 작용할 수 있다는 분석이 나온다.", KR, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "co_listed" for row in result))

    def test_the_canonical_pair_still_passes_after_the_new_rules(self):
        cos = [company("012450", "한화에어로스페이스"), company("047810", "한국항공우주", "KAI")]
        result = article_relations(
            "협약", "한화에어로스페이스와 KAI는 상호협력 양해각서(MOU)를 체결했다고 밝혔다.", cos, "ko")
        self.assertTrue(result)
        self.assertIsNone(result[0]["reject"])

    def test_a_name_that_continues_the_second_company_is_not_a_third_party(self):
        """'현대차그룹은 … 구글 딥마인드와 협력해' - 딥마인드 is the rest of Google's
        name, not a counterparty. Read as third_party on the 400-batch sample."""
        cos = [company("005380", "현대차", "현대차그룹"), company("GOOGL", "Alphabet", "구글", market="NASDAQ")]
        result = article_relations(
            "로봇", "현대차그룹은 2026년 1월 구글 딥마인드와 협력해 아틀라스에 제미나이 로보틱스를 "
                    "적용하는 개발 체계를 구축했다.", cos, "ko")
        self.assertTrue(result)
        self.assertIsNone(result[0]["reject"])

    def test_a_list_that_runs_on_past_the_pair_before_등_is_a_roster(self):
        """'미래에셋증권과 한국투자증권, 신한투자증권 등도 … 관련 기업과 협력' - 과 joins the
        first two, but the list keeps going and 등 closes it. Accepted on the live pass."""
        cos = [company("006800", "미래에셋증권"), company("071050", "한국금융지주", "한국투자증권")]
        result = article_relations(
            "토큰증권", "미래에셋증권과 한국투자증권, 신한투자증권 등도 블록체인·토큰증권 관련 "
                      "기업과 협력하거나 자체 플랫폼 구축을 추진하고 있다.", cos, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "co_listed" for row in result))

    def test_a_list_mark_between_the_names_makes_a_roster_unless_the_first_is_the_subject(self):
        cos = [company("055550", "신한지주", "신한은행"), company("035720", "카카오")]
        result = article_relations(
            "협약", "도는 이날 오전 신한은행·강원신용보증재단과 특별 금융지원 협약을, 오후에는 "
                    "카카오·우아한형제들과 상생협력 협약을 맺었다.", cos, "ko")
        self.assertTrue(result)
        self.assertTrue(all(row["reject"] == "co_listed" for row in result))

    def test_a_subject_whose_counterparties_are_a_list_keeps_the_pair(self):
        """'HD현대는 … 테라파워·현대건설과 협력을 넓힐 계획' - the roster is who HD현대 works
        with; HD현대↔현대건설 is stated."""
        cos = [company("267250", "HD현대"), company("000720", "현대건설")]
        result = article_relations(
            "원전", "HD현대는 설비의 적기 공급을 추진하고, 테라파워·현대건설과 향후 원전 설계와 "
                    "건설 분야에서 협력을 넓힐 계획이다.", cos, "ko")
        self.assertTrue(result)
        self.assertIsNone(result[0]["reject"])

    def test_a_grammatical_pair_with_등_is_not_co_listed(self):
        """'A와 B 등이 공동 개발' - 와 makes them parties; 등 only says there were more."""
        result = article_relations(
            "제목", "삼성전자와 SK하이닉스 등이 차세대 메모리를 공동 개발한다.", KR, "ko")
        self.assertTrue(result)
        self.assertFalse(any(row["reject"] == "co_listed" for row in result))

if __name__ == "__main__":
    unittest.main()


class EarlyExitTest(unittest.TestCase):
    """Fewer than two certain companies means no pair, so the sentence catalog is
    never built. It used to be built first: 8 seconds per long article for []."""

    def test_one_company_never_reaches_the_sentence_splitter(self):
        from unittest.mock import patch
        import relations
        with patch.object(relations, "sentence_view", side_effect=AssertionError("split called")) as split:
            result = article_relations("제목", "삼성전자는 이익이 늘었다. " * 2000,
                                       [company("005930", "삼성전자")], "ko")
        self.assertEqual(result, [])
        split.assert_not_called()

    def test_two_companies_still_go_through_the_splitter(self):
        result = article_relations("제목", "삼성전자가 SK하이닉스와 협력을 강화한다.", KR, "ko")
        self.assertTrue(result)
