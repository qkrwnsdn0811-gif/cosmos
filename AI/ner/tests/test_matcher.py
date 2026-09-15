import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from matcher import CompanyMatcher  # noqa: E402

ALIASES = HERE / "data" / "aliases.csv"


def tickers(res):
    return {m.ticker for m in res.mentions}


class MatcherRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ALIASES.exists():
            subprocess.run([sys.executable, str(HERE / "build_aliases.py")], check=True)
        cls.m = CompanyMatcher.from_csv(ALIASES)

    def test_basic_and_particle(self):
        r = self.m.match("삼성전자, HBM 투자 확대", "삼성전자는 이날 밝혔다.")
        self.assertEqual(tickers(r), {"005930"})
        self.assertEqual(r.by_ticker()[0]["n_mentions"], 2)
        self.assertEqual(r.by_ticker()[0]["first_pos"], "title")

    def test_preferred_stock_blocked(self):
        r = self.m.match("", "삼성전자우 강세, 삼성전자우B도 상승")
        self.assertEqual(tickers(r), set())
        self.assertTrue(any(b[0].startswith("삼성전자우") for b in r.blocked))

    def test_hybrid_not_hybe(self):
        r = self.m.match("", "하이브리드 차량 판매가 늘었다.")
        self.assertEqual(tickers(r), set())
        r = self.m.match("", "하이브가 신인 그룹을 데뷔시켰다.")
        self.assertEqual(tickers(r), {"352820"})

    def test_latin_boundary(self):
        r = self.m.match("", "SKT와 KTX 요금 인상")
        self.assertNotIn("030200", tickers(r))          # KT inside SKT / KTX must not match KT
        self.assertIn("017670", tickers(r))             # but SKT itself is an SK텔레콤 alias
        r = self.m.match("", "KT는 통신비 할인을 발표했다.")
        self.assertEqual(tickers(r), {"030200"})

    def test_skt_alias(self):
        r = self.m.match("", "SKT, 5G 요금제 개편")
        self.assertEqual(tickers(r), {"017670"})

    def test_sports_team_blocked(self):
        r = self.m.match("한화 이글스 4연승", "한화 이글스가 LG 트윈스를 꺾었다.")
        self.assertEqual(tickers(r), set())

    def test_group_resolution(self):
        # group name + member present -> attributed to member only
        r = self.m.match("", "삼성이 발표했다. 삼성전자는 실적을 공개했다.")
        self.assertEqual(tickers(r), {"005930"})
        # group name alone with holding ticker -> low confidence holding
        r = self.m.match("", "LG가 신사업을 발표했다.")
        self.assertEqual(tickers(r), {"003550"})
        self.assertEqual(r.mentions[0].confidence, 0.5)
        # group name alone without holding ticker -> unresolved
        r = self.m.match("", "삼성이 발표했다.")
        self.assertEqual(tickers(r), set())
        self.assertEqual([u.alias for u in r.unresolved], ["삼성"])

    def test_unlisted_subsidiary_blocked(self):
        r = self.m.match("", "SK온은 배터리 공장을 짓는다. GS칼텍스도 참여한다.")
        self.assertEqual(tickers(r), set())

    def test_longest_match(self):
        r = self.m.match("", "LG에너지솔루션과 LG전자, 포스코퓨처엠이 참여")
        self.assertEqual(tickers(r), {"373220", "066570", "003670"})

    def test_ticker_context(self):
        r = self.m.match("", "삼성전자(005930)는 3% 올랐다.")
        self.assertEqual(tickers(r), {"005930"})
        r = self.m.match("", "황반변성(AMD) 치료제 개발")            # acronym, not the chip maker
        self.assertEqual(tickers(r), set())
        r = self.m.match("", "엔비디아(NVDA) 주가 급등")
        self.assertEqual(tickers(r), {"NVDA"})
        r = self.m.match("", "$NVDA is up 5% while NASDAQ: AAPL fell.")
        self.assertEqual(tickers(r), {"NVDA", "AAPL"})

    def test_nasdaq_korean_names(self):
        r = self.m.match("", "애플과 마이크로소프트, 테슬라가 하락했다.")
        self.assertEqual(tickers(r), {"AAPL", "MSFT", "TSLA"})

    def test_english_news(self):
        r = self.m.match("Nvidia beats estimates", "Samsung Electronics and SK hynix supply HBM to Nvidia.")
        self.assertEqual(tickers(r), {"NVDA", "005930", "000660"})

    def test_short_ascii_group_needs_korean_doc(self):
        r = self.m.match("", "Goldman Sachs ( GS ) and Morgan Stanley ( MS ) rose. KB Home fell.")
        self.assertEqual(tickers(r), set())
        r = self.m.match("", "GS는 신사업을 발표했다.")
        self.assertEqual(tickers(r), {"078930"})

    def test_sports_gate(self):
        r = self.m.match("류현진 4실점", "KBO 플레이오프 4차전에서 한화의 선발투수로 등판했다. 삼성과의 경기에서 3이닝을 던졌다.")
        self.assertTrue(r.is_sports)
        self.assertEqual(tickers(r), set())
        self.assertIn("한화", [u.alias for u in r.unresolved])

    def test_alphabet_korean_word(self):
        r = self.m.match("", "알파벳 순서로 정렬한다.")
        self.assertEqual(tickers(r), set())          # unresolved, not a company
        r = self.m.match("", "구글 모회사 알파벳이 실적을 발표했다.")
        self.assertEqual(tickers(r), {"GOOGL"})
        self.assertEqual(r.by_ticker()[0]["n_mentions"], 1)  # 알파벳 absorbed into 구글

    def test_product_names_need_company(self):
        r = self.m.match("", "김 의원은 페이스북에 글을 올렸다. 아이폰으로 촬영했다.")
        self.assertEqual(tickers(r), set())
        self.assertEqual({u.alias for u in r.unresolved}, {"페이스북", "아이폰"})
        r = self.m.match("", "메타는 페이스북과 인스타그램에 새 기능을 넣었다.")
        self.assertEqual(tickers(r), {"META"})
        self.assertEqual(r.by_ticker()[0]["n_mentions"], 3)
        self.assertIn("product", r.by_ticker()[0]["method"])

    def test_industry_hits_without_company(self):
        r = self.m.match("반도체 수출 30% 증가", "반도체 업황 개선으로 조선업계도 기대감이 커졌다. 조선일보는 이를 보도했다.")
        self.assertEqual(tickers(r), set())
        inds = {i["industry_id"] for i in r.by_industry()}
        self.assertEqual(inds, {"SEMI", "TRANSPORT"})
        self.assertEqual([i for i in r.by_industry() if i["industry_id"] == "SEMI"][0]["first_pos"], "title")
        self.assertTrue(any(b[0] == "조선일보" for b in r.blocked))

    def test_industry_with_company_both_reported(self):
        r = self.m.match("", "삼성전자가 HBM 공급을 늘린다.")
        self.assertEqual(tickers(r), {"005930"})
        self.assertEqual({i["industry_id"] for i in r.by_industry()}, {"SEMI"})

    def test_name_equals_ticker(self):
        r = self.m.match("", "엔비디아의 H100, AMD의 MI300 시리즈가 HBM과 결합된다.")
        self.assertEqual(tickers(r), {"NVDA", "AMD"})

    def test_person_name_not_kt(self):
        r = self.m.match("", "케이티 맥낼리(미국) 조를 상대로 승리했다.")
        self.assertEqual(tickers(r), set())

    def test_sk_compound_not_sk(self):
        r = self.m.match("", "에브리봇이 SK인텔릭스 웰니스 로봇에 AI 플랫폼을 이식한다.")
        self.assertEqual(tickers(r), set())

    def test_hanwha_corp_itself(self):
        r = self.m.match("", "김승연 회장이 ㈜한화 지분 11.32%를 증여했다. 한화에어로스페이스도 언급됐다.")
        self.assertEqual(tickers(r), {"000880", "012450"})

    def test_boilerplate_trimmed(self):
        body = "GM 실버라도 EV가 신기록을 세웠다. " * 20 + "#전기픽업트럭 #전기차기술 #맥스레인지 #전기차효율\n\n● 현대차, 2026 싼타페 출시\nblog.naver.com"
        r = self.m.match("GM 실버라도 EV 신기록", body)
        self.assertEqual(tickers(r), set())
        self.assertIsNotNone(r.trimmed_at)
        body = "Universal Health Services reported results. " * 30 + "Stocks that made our list in 2020 include Nvidia (+1,545%)."
        r = self.m.match("Analyst questions", body)
        self.assertEqual(tickers(r), set())
        # a genuine mention early in the text is kept even when a marker appears later
        r = self.m.match("엔비디아 실적", "엔비디아가 실적을 발표했다. " * 20 + "\n관련기사 삼성전자 …")
        self.assertEqual(tickers(r), {"NVDA"})

    def test_short_korean_marker_needs_line_start(self):
        # "관련 기사" 같은 짧은 마커는 본문 속 표현으로도 쓰인다. 줄머리에 있을 때만 잘라야
        # 기사 중간의 "관련 기사로는 …" 문장이 뒤 본문을 통째로 날리지 않는다.
        mid = self.m.match("엔비디아 실적", "엔비디아가 실적을 발표했다. " * 20
                           + "관련 기사에서 다룬 삼성전자의 대응도 눈여겨볼 만하다.")
        self.assertEqual(tickers(mid), {"NVDA", "005930"})
        self.assertIsNone(mid.trimmed_at)
        head = self.m.match("엔비디아 실적", "엔비디아가 실적을 발표했다. " * 20
                            + "\n관련 기사\n삼성전자 HBM 양산")
        self.assertEqual(tickers(head), {"NVDA"})
        self.assertIsNotNone(head.trimmed_at)

    def test_sports_sponsor_downgraded(self):
        body = "김연경 감독이 IBK 기업은행 알토스전 첫 패배 후 선수들과 미팅했다. 우리가 리시브는 기업은행보다 잘했다. 세터들이 안 좋았다."
        r = self.m.match("김연경, 창단 첫 패배 후 독설", body)
        self.assertTrue(r.is_sports)
        strong = {m.ticker for m in r.mentions if m.confidence >= 0.5}
        self.assertEqual(strong, set())
        r = self.m.match("", "기업은행이 중소기업 대출 금리를 내렸다.")
        self.assertEqual(tickers(r), {"024110"})

    def test_inserted_headlines_masked(self):
        body = ("Onsemi announced a buyback. " * 10 + "\n→ Intel's Black Friday Breakout: Apple Rumors Fuel a Holiday Rally\n"
                + "The program starts in 2026. " * 10 + "\nIs Meta Stock a Buy After the Dip?\n" + "Analysts were split. " * 10)
        r = self.m.match("onsemi buyback", body)
        self.assertEqual(tickers(r), set())
        r = self.m.match("", "Intel reported record data-center revenue on Thursday.")
        self.assertEqual(tickers(r), {"INTC"})

    def test_kia_and_hyundai(self):
        r = self.m.match("", "현대차·기아는 EV 판매를 늘렸다. 현대적인 디자인이다.")
        self.assertEqual(tickers(r), {"005380", "000270"})


    def test_krw_conversion_is_not_hanwha(self):
        # 韓貨: a won conversion of a foreign amount, not the Hanwha group
        for body in ("영국에서 커피를 버린 여성이 150파운드(한화 약 29만원)의 과태료를 부과받았다.",
                     "지난해 K푸드 수출액은 70억2000만달러(한화 약 10조849억원)로 집계됐다.",
                     "총가격 2,200엔 (한화 약 2만 원대) 수준이었다.",
                     "이 회사의 상반기 매출은 10억6471만달러로 한화 1조4780억원에 이른다."):
            r = self.m.match("", body)
            self.assertNotIn("000880", tickers(r), body)

    def test_hanwha_company_still_matches(self):
        r = self.m.match("", "한화는 계열사 엣지코어피에프브이의 PF 대출을 위해 담보를 제공했다.")
        self.assertIn("000880", tickers(r))
        r = self.m.match("", "한화가 3조원 규모 수주를 따냈다.")
        self.assertIn("000880", tickers(r))

    def test_strategy_is_not_microstrategy(self):
        # "Strategy, Inc." must stay one alias; a bare "Strategy" would match everyday English
        for body in ("AI CIC장에 유경상 전 전사전략(Corp. Strategy)센터장이 선임됐다.",
                     "ESG(Enterprise Strategy Group)의 검증을 받았다.",
                     "투자 회수(Exit Strategy)를 검토 중이다."):
            r = self.m.match("", body)
            self.assertNotIn("MSTR", tickers(r), body)

    def test_stadium_name_does_not_erase_the_company(self):
        # one team name with no other sports signal must not downgrade the whole document
        r = self.m.match("KT, 갤럭시 Z 폴더블7 AI 체험존 운영",
                         "KT가 체험존을 연다. " * 20 + "수원 KT 위즈파크점, KT플라자 동성로점 등 네 곳에서 운영한다. "
                         + "KT는 MZ세대를 겨냥했다고 밝혔다. " * 20)
        strong = {t["ticker"] for t in r.by_ticker() if t["confidence"] >= 0.5}
        self.assertIn("030200", strong)

    def test_match_recap_still_downgraded(self):
        r = self.m.match("GS칼텍스 개막전 쾌승…기업은행 3-1 제압 [V-리그]",
                         "여자부 개막 경기에서 GS칼텍스가 기업은행을 세트스코어 3-1로 제압했다. "
                         "기업은행은 리시브가 흔들렸다. 감독은 선수 기용을 아쉬워했다.")
        strong = {t["ticker"] for t in r.by_ticker() if t["confidence"] >= 0.5}
        self.assertNotIn("024110", strong)

    def test_transliteration_plural_variants(self):
        # 국내 기사는 영어 복수 -s 를 '즈'/'스' 둘 다로 적는다. 사전에는 '즈'만 있었다.
        for body, want in (
            ("미국 시스코시스템스 또한 이달 초 인도에서 제조를 시작하겠다고 밝혔다.", "CSCO"),
            ("시스코시스템즈는 네트워크 장비 업체다.", "CSCO"),
            ("베이커휴스는 유전 서비스 업체다.", "BKR"),
            ("크래프트하인스의 실적이 발표됐다.", "KHC"),
        ):
            self.assertIn(want, tickers(self.m.match("", body)), body)

    def test_plural_derivation_does_not_touch_korean_names(self):
        # 국내 법인명의 '즈'는 음차가 아니다. '스' 표기는 실존하지 않으므로 파생하면 안 된다.
        r = self.m.match("", "코스닥에서 SK머티리얼즈와 카카오게임즈가 올랐다.")
        for bad in ("SK머티리얼스", "카카오게임스"):
            self.assertNotIn(bad, {m.alias for m in r.mentions}, bad)
        # 지명·일반명사와의 충돌도 없어야 한다
        r = self.m.match("", "(샌프란시스코=연합뉴스) 김태종 특파원")
        self.assertEqual(tickers(r), set())

    def test_warner_bros_short_form(self):
        # full200 에서 '워너브라더스'는 0회, '워너브로스'만 3회다 — 사전이 용례와 반대였다
        r = self.m.match("", "워너브로스는 파라마운트글로벌과의 인수합병을 중단하기로 했다.")
        self.assertIn("WBD", tickers(r))

    def test_hyphenated_joint_venture_is_blocked(self):
        # 'LG-BCM' 은 구미 양극재 합작법인이지 지주회사 LG 가 아니다. 같은 문장의 'SK실트론'이
        # 이미 blockers.txt 로 처리돼 있어 동일 계층에서 다룬다 (경계 규칙은 건드리지 않는다).
        r = self.m.match("", "이차전지 양극재 공장 LG-BCM과 반도체 웨이퍼 생산기업 SK실트론 등 대기업을 유치했다.")
        self.assertNotIn("003550", tickers(r))
        self.assertNotIn("034730", tickers(r))
        # 하이픈으로 이은 정당한 병기는 그대로 잡혀야 한다
        r = self.m.match("", "삼성전자-SK하이닉스 협력이 논의됐다.")
        self.assertEqual(tickers(r), {"005930", "000660"})
        r = self.m.match("", "LG전자가 신제품을 내놨다.")
        self.assertIn("066570", tickers(r))

    def test_broker_shorthand_not_holding_company(self):
        # 뉴스는 증권 계열사를 그룹 약칭으로 부른다. 이때 '신한'은 신한투자증권(202종목 밖)이지
        # 신한지주가 아니다. 열거 안에서 사전에 있는 것만 비대칭으로 잡히던 문제를 0으로 만든다.
        r = self.m.match("", "두산밥캣은 삼성·신한·교보·다올 등 실적 발표 이후 보고서를 발간한 "
                             "4개 증권사가 모두 목표 주가를 올렸다.")
        self.assertNotIn("055550", tickers(r))
        r = self.m.match("", "▲실권주청약=엘앤에프(066970) 상세보기(주관사 KB) ▲보통주추가상장")
        self.assertNotIn("105560", tickers(r))

    def test_broker_context_keeps_real_mentions(self):
        # 증권 계열사가 202종목 안에 있으면 증권사 문맥은 억제 근거가 아니라 확증 근거다
        r = self.m.match("", "미래에셋·NH 등 9개 증권사와 시장조성자 계약을 체결했다.")
        self.assertIn("006800", tickers(r))
        # 은행 나열은 증권사 문맥이 아니다
        r = self.m.match("", "이에 따라 신한, KB국민, NH농협, 하나, 우리은행 등은 대출 지표를 점검해야 한다.")
        self.assertIn("055550", tickers(r))
        # 기사 주제가 지주사면 같은 문서에 증권사 얘기가 있어도 살아남는다
        r = self.m.match("", "신한지주는 3분기 순이익이 늘었다고 밝혔다. 증권사들은 목표주가를 상향했다.")
        self.assertIn("055550", tickers(r))

    def test_realty_bot_apartment_name(self):
        body = ("국토교통부 실거래가 공개시스템에 따르면 지난 12월 초순 '두산'의 전용 59.97㎡은 2건이 "
                "거래됐으며 중위거래가격은 5억4,250만원이다. 서울특별시 성북구 석관동에 자리한 '두산'은 "
                "1998년 완공된 14개동 총 1,129세대 규모의 단지다. "
                "[이 기사는 부동산 시세분석 전문기자 서경부동산뉴스봇이 실시간으로 작성했습니다.]")
        r = self.m.match("'두산'(서울특별시 성북구) 전용 59.97㎡ 실거래가 평균 5억4,250만원", body)
        self.assertTrue(r.is_realty_bot)
        self.assertNotIn("000150", tickers(r))

    def test_realty_gate_keeps_builder_mentions(self):
        # 게이트는 그룹 약칭 귀속만 막는다. 시공사로 정식명이 나온 건 정탐이다.
        r = self.m.match("", "국토교통부 실거래가 공개시스템 기준 중위거래가격을 보면 "
                             "삼성물산 '래미안 포레스티지' 총 4043가구가 포함된다.")
        self.assertTrue(r.is_realty_bot)
        self.assertIn("028260", tickers(r))

    def test_realty_gate_needs_more_than_a_byline(self):
        # 바이라인 한 줄만으로는 사람이 쓴 부동산 기사를 게이트하면 안 된다
        r = self.m.match("", "두산이 분양한 아파트가 인기를 끌었다고 부동산뉴스봇 기자가 전했다.")
        self.assertFalse(r.is_realty_bot)

    def test_team_name_variants_blocked(self):
        # 리터럴 blocker 는 토큰 사이에 뭐가 끼면 뚫린다. 스폰서+마스코트 사이를 유연하게 본다.
        r = self.m.match("", "프로 야구단 '기아(KIA) 타이거즈'와 협업해 유니폼을 착용한 피규어를 제작한다.")
        self.assertNotIn("000270", tickers(r))
        r = self.m.match("", "하위권 탈출이 시급한 삼성화재 블루팡스를 4연패에 빠뜨렸다.")
        self.assertNotIn("000810", tickers(r))

    def test_org_separators_are_not_team_separators(self):
        # 하이픈·중점은 조직 병기용이다. 구단명 내부 구분자로 보면 정상 매칭이 죽는다.
        self.assertIn("000270", tickers(self.m.match("", "기아-교육부 업무협약이 체결됐다.")))
        self.assertIn("055550", tickers(self.m.match("", "신한·하나 금융지주가 실적을 발표했다.")))

    def test_flexible_team_rule_does_not_overreach(self):
        # '알토스'는 알토스벤처스와 충돌하고 '핸드볼'은 일반 종목명이라 마스코트에서 뺐다.
        # 기존 리터럴 blocker 가 실제 구단만 계속 막는다.
        self.assertNotIn("024110", tickers(self.m.match("", "김연경이 IBK 기업은행 알토스전에서 패했다.")))
        r = self.m.match("", "알토스벤처스가 이 스타트업에 투자했다.")
        self.assertEqual(tickers(r), set())

    def test_amazon_place_not_company(self):
        for body in ("남미 에콰도르의 아마존 열대우림 지역에서 아나콘다 신종이 발견됐다.",
                     "아마존 밀림의 원주민 부족이 시위를 벌였다.",
                     "Deforestation in the Amazon rainforest slowed last year."):
            self.assertNotIn("AMZN", tickers(self.m.match("", body)), body)

    def test_amazon_company_survives(self):
        # 앵커 방식이라 조사가 붙거나 뒤에 자연 어휘가 없으면 그대로 잡혀야 한다.
        for body in ("아마존은 AWS 매출이 늘었다고 밝혔다.",
                     "아마존과 환경단체가 열대우림 보호 협약을 맺었다.",
                     "아마존에서 다큐멘터리를 스트리밍한다.",
                     "아마존 생태계에서 셀러들이 이탈하고 있다.",
                     "Amazon reported record cloud revenue."):
            self.assertIn("AMZN", tickers(self.m.match("", body)), body)

    def test_apr_interest_rate_not_ticker(self):
        self.assertNotIn("278470", tickers(self.m.match("", "연이율 APR 19.99%가 적용된다.")))
        self.assertNotIn("278470", tickers(self.m.match("", "리볼빙 APR은 법정 최고금리에 가깝다.")))
        # 증시 표기 "<종목> N%" 는 죽이면 안 된다 — 한국 기사의 표준 표기다
        self.assertIn("278470", tickers(self.m.match("", "APR 3.2% 상승 마감했다.")))

    def test_strategy_compound_not_microstrategy(self):
        for body in ("콘텐츠 스트래티지 담당 임원을 영입했다.", "브랜드 스트래티지 워크숍을 열었다."):
            self.assertNotIn("MSTR", tickers(self.m.match("", body)), body)
        self.assertIn("MSTR", tickers(self.m.match("", "마이크로스트래티지가 비트코인을 추가 매입했다.")))
        # MSTR 은 비즈니스 인텔리전스 기업이라 '데이터/디지털'은 부정 문맥이 아니다
        self.assertIn("MSTR", tickers(self.m.match("", "디지털 자산 전략으로 스트래티지가 주목받는다.")))

    def test_orion_space_not_confectionery(self):
        self.assertNotIn("271560", tickers(self.m.match("", "NASA Orion capsule returned to Earth.")))
        self.assertIn("271560", tickers(self.m.match("", "Orion posted higher quarterly profit.")))

    def test_ellipsis_trailing_anchor(self):
        # 마지막 항목에만 접미사를 붙이는 한국어 나열. 앞 항목들이 통째로 미탐이었다.
        r = self.m.match("", "이에 따라 신한, KB국민, NH농협, 하나, 우리은행 등은 대출 지표를 점검해야 한다.")
        for want in ("105560", "086790", "316140"):
            self.assertIn(want, tickers(r), want)

    def test_ellipsis_parenthetical_list(self):
        r = self.m.match("", "평균 보수액은 5대 시중은행(KB국민·신한·하나·우리·NH농협)과 크게 다르지 않다.")
        for want in ("105560", "055550", "086790", "316140"):
            self.assertIn(want, tickers(r), want)

    def test_ellipsis_hyundai_kia_idiom(self):
        r = self.m.match("", "조합원이 8만명이 넘는 현대·기아차 노조도 파업을 예고하고 있다.")
        self.assertIn("005380", tickers(r))
        self.assertIn("000270", tickers(r))

    def test_ellipsis_ambiguous_head_needs_company(self):
        # '우리'·'하나'는 일반명사다. 같은 나열에서 둘 이상이 해결될 때만 전개한다.
        r = self.m.match("", "정부와 우리, 미국 등 주요 은행이 참여했다.")
        self.assertNotIn("316140", tickers(r))
        r = self.m.match("", "하나 남은 은행 점포가 문을 닫는다.")
        self.assertNotIn("086790", tickers(r))

    def test_ellipsis_marks_method_for_audit(self):
        r = self.m.match("", "신한, KB국민, 하나, 우리은행 등이 참여했다.")
        expanded = [m for m in r.mentions if m.method == "ellipsis"]
        self.assertTrue(expanded)
        for m in expanded:
            self.assertLess(m.confidence, 1.0)   # 사전 직접 매칭보다 낮게 둬 감사 가능하게
            self.assertEqual(r.text[m.start:m.end], m.alias[:m.end - m.start])

if __name__ == "__main__":
    unittest.main()
