from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from nexus.cases.newstype import OTHER, classify_text
from nexus.cases.reaction import (HOLD_FEW, HOLD_NO_TYPE, HOLD_SMALL, HOLD_SPLIT, HOLD_WEAK_LINK, MoveBook,
                                  SignalBook, backtest, case_frame, move_backtest, position_of, reaction_stats,
                                  unique_cases)
from nexus.models import Article, CompanyMention, EventBundle, Impact, PathStep

KST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("title, code", [
    ("美, 中 반도체 수출통제 강화…장비주 긴장", "EXPORT_CONTROL"),
    ("삼성전자, 1분기 영업이익 사상 최대", "EARNINGS_BEAT"),
    ("SK하이닉스 어닝쇼크…적자전환", "EARNINGS_MISS"),
    ("한미반도체, 2000억 규모 공급계약 체결", "SUPPLY_DEAL"),
    ("D램 고정거래가격 3개월 연속 상승", "PRICE_UP"),
    ("평택 공장 화재로 생산 차질", "ACCIDENT"),
    ("코스피, 외국인 순매수에 상승 마감", "MARKET_WRAP"),
    ("삼성전자 대졸 공채 시작", OTHER),
])
def test_classify_title(title, code):
    assert classify_text(title, "").code == code


def test_specific_event_beats_market_wrap_on_tie():
    # one title hit each: the export control is the event, the index is the frame
    assert classify_text("수출규제 여파에 코스피 하락", "").code == "EXPORT_CONTROL"


def test_one_body_hit_is_not_enough():
    result = classify_text("반도체 업계 동향", "관세 이야기가 나왔다")
    assert result.code == OTHER and result.scores == {"TARIFF": 1}


def test_confidence_is_share_of_scores():
    result = classify_text("HBM 공급계약 체결", "")
    assert result.code == "SUPPLY_DEAL"
    assert result.confidence == pytest.approx(6 / 9)    # SUPPLY_DEAL 2 title hits, AI_DEMAND 1


def test_position_of():
    assert position_of("A", "REPORTED", ()) == "REPORTED"
    supply = (PathStep("S", "C", "SUPPLY"),)
    assert position_of("S", "INFERRED", supply) == "SUPPLIER"
    assert position_of("C", "INFERRED", supply) == "CUSTOMER"
    assert position_of("B", "INFERRED", (PathStep("A", "B", "COMPETE"),)) == "COMPETITOR"
    assert position_of("B", "INFERRED", (PathStep("A", "B", "CO_MENTIONED"),)) == "COMENTION"
    two_hops = (PathStep("X", "A", "SUPPLY"), PathStep("A", "B", "COMPETE"))
    assert position_of("B", "INFERRED", two_hops) == "COMPETITOR"


def _article(news_id: str, day: date) -> Article:
    return Article(news_id, "t", "c", day, datetime(day.year, day.month, day.day, 9, tzinfo=KST), "p", "", (), (), "",
                   (), (), ())


def test_case_frame_and_dedup():
    day = date(2026, 1, 5)
    impacts = [Impact("A", "REPORTED", 0, (), 1.0, "OK", None),
               Impact("B", "INFERRED", 1, (PathStep("B", "A", "SUPPLY"),), 0.5, "OK", None),
               Impact("C", "INFERRED", 1, (PathStep("A", "C", "SUPPLY"),), 0.2, "HOLD", "low")]
    bundles = [EventBundle(_article(n, day), "SEMICONDUCTOR", [CompanyMention("A", 1.0, ("title",))], impacts,
                           trading_date=day) for n in ("n1", "n2")]
    bundles.append(EventBundle(_article("n3", day), "SEMICONDUCTOR", [], impacts, trading_date=None))
    frame = case_frame(bundles, {"n1": "SUPPLY_DEAL", "n2": "SUPPLY_DEAL"},
                       lambda key, t0: {1: 0.01, 3: -0.02} if key != "C" else {})
    assert len(frame) == 6                                   # n3 has no trading day
    assert frame.loc[frame.company_key == "B", "position"].iloc[0] == "SUPPLIER"
    assert pd.isna(frame.loc[frame.company_key == "A", "ar_20"].iloc[0])
    cases = unique_cases(frame)
    assert sorted(cases.company_key) == ["A", "B"]           # same day twice → once; HOLD dropped


def _cases(values: list[float], *, news_type="EARNINGS_MISS", position="REPORTED", company="A", day=date(2026, 1, 5)):
    return pd.DataFrame([{"news_id": f"n{i}", "company_key": company, "trading_date": day + timedelta(days=i),
                          "news_type": news_type, "position": position, "status": "OK",
                          "ar_1": v, "ar_3": v, "ar_5": v, "ar_20": v} for i, v in enumerate(values)])


def test_reaction_stats_pooled_and_by_company():
    cases = pd.concat([_cases([-0.02, -0.03, 0.01]), _cases([0.04], company="B")])
    stats = reaction_stats(cases)
    pooled = stats[stats.company_key.isna() & (stats.window_days == 3)].iloc[0]
    assert (pooled.case_count, pooled.down_count, pooled.up_count) == (4, 2, 2)
    assert pooled.mean_ar == pytest.approx(0.0)
    own = stats[(stats.company_key == "A") & (stats.window_days == 3)].iloc[0]
    assert own.case_count == 3 and own.median_ar == pytest.approx(-0.02)


def test_reaction_stats_uses_training_period_only():
    cases = pd.concat([_cases([-0.02] * 3), _cases([0.05] * 3, day=date(2026, 8, 1))])
    stats = reaction_stats(cases)
    assert stats[stats.company_key.isna() & (stats.window_days == 3)].iloc[0].case_count == 3


def test_signal_rules():
    down = SignalBook(reaction_stats(_cases([-0.02] * 8 + [0.01] * 2)))
    s = down.signal("EARNINGS_MISS", "REPORTED", "A", "SHORT")
    assert (s.label, s.scope, s.case_count, s.same_share) == ("DOWN", "COMPANY", 10, 0.8)
    assert down.signal("EARNINGS_MISS", "REPORTED", "A", "LONG").hold_reason == HOLD_SMALL   # mean −1.4% < 2%
    assert SignalBook(reaction_stats(_cases([-0.02] * 9))).signal("EARNINGS_MISS", "REPORTED", "A", "SHORT").hold_reason == HOLD_FEW
    split = SignalBook(reaction_stats(_cases([-0.03] * 6 + [0.03] * 4)))
    assert split.signal("EARNINGS_MISS", "REPORTED", "A", "SHORT").hold_reason == HOLD_SPLIT
    small = SignalBook(reaction_stats(_cases([-0.005] * 10)))
    assert small.signal("EARNINGS_MISS", "REPORTED", "A", "SHORT").hold_reason == HOLD_SMALL
    assert down.signal("OTHER", "REPORTED", "A", "SHORT").hold_reason == HOLD_NO_TYPE
    assert down.signal("EARNINGS_MISS", "REPORTED", "A", "SHORT", status="HOLD").hold_reason == HOLD_WEAK_LINK


def test_signal_falls_back_to_position_when_company_has_few_cases():
    cases = pd.concat([_cases([-0.03] * 9, company="B"), _cases([-0.03] * 3, company="A")])
    s = SignalBook(reaction_stats(cases)).signal("EARNINGS_MISS", "REPORTED", "A", "SHORT")
    assert (s.label, s.scope, s.case_count) == ("DOWN", "POSITION", 12)


def test_backtest_counts_hits_after_training():
    train = _cases([-0.03] * 10)
    test = _cases([-0.01, -0.02, 0.04], day=date(2026, 8, 1))
    result = backtest(pd.concat([train, test]), SignalBook(reaction_stats(train)))
    short = result[(result.horizon == "SHORT") & result.news_type.isna()].iloc[0]
    assert (short.case_count, short.labeled_count, short.hit_count, short.down_labels) == (3, 3, 2, 3)
    assert short.base_down_share == pytest.approx(2 / 3)

def test_move_book_uses_size_per_company_and_grades_in_thirds():
    cases = pd.concat([_cases([0.01, -0.01] * 5, company="A"), _cases([0.05, -0.05] * 5, company="B"),
                       _cases([0.03, -0.03] * 5, company="C"), _cases([0.2] * 9, company="D"),
                       # same day as A's first case with another type: counted once
                       _cases([0.4], company="A", news_type="SUPPLY_DEAL")])
    book = MoveBook(cases)
    a = book.move("A", "SHORT")
    assert (a.expected, a.case_count, a.grade) == (pytest.approx(0.01), 10, "SMALL")
    assert book.move("C", "SHORT").grade == "MID" and book.move("B", "LONG").grade == "LARGE"
    assert book.move("D", "SHORT") is None                  # 9 days < MIN_CASES


def test_move_backtest_compares_grades_after_training():
    train = pd.concat([_cases([0.01, -0.01] * 5, company="A"), _cases([0.03, -0.03] * 5, company="C"),
                       _cases([0.05, -0.05] * 5, company="B")])
    test = pd.concat([_cases([0.02, -0.04], company="A", day=date(2026, 8, 1)),
                      _cases([-0.08], company="B", day=date(2026, 8, 1))])
    result = move_backtest(pd.concat([train, test]), MoveBook(train))
    short = result[result.horizon == "SHORT"].set_index("grade")
    assert short.loc["SMALL", "case_count"] == 2 and short.loc["SMALL", "realized_move"] == pytest.approx(0.03)
    assert short.loc["LARGE", "predicted_move"] == pytest.approx(0.05)
    assert short.loc["LARGE", "realized_move"] == pytest.approx(0.08)
    assert short.loc["MID", "case_count"] == 0

def test_grades_hold_only_when_realized_moves_grow_with_grade():
    from nexus.cases.report import grades_hold
    rows = pd.DataFrame([("SHORT", "SMALL", 1, 0.03, 0.04), ("SHORT", "MID", 1, 0.05, 0.06), ("SHORT", "LARGE", 1, 0.06, 0.07),
                         ("LONG", "SMALL", 1, 0.08, 0.09), ("LONG", "MID", 1, 0.12, 0.16), ("LONG", "LARGE", 1, 0.18, 0.14)],
                        columns=["horizon", "grade", "case_count", "predicted_move", "realized_move"])
    assert grades_hold(rows, "SHORT") is True
    assert grades_hold(rows, "LONG") is False
