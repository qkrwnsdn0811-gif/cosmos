from datetime import date, datetime, timedelta, timezone

from nexus.cases.feed import feed_times, main_companies, main_company
from nexus.models import Article, CompanyMention, EventBundle

KST = timezone(timedelta(hours=9))
SK, SS, NX, HM = "KOSPI:000660", "KOSPI:005930", "KOSDAQ:348210", "KOSPI:042700"


def _bundle(news_id: str, at: datetime, mentions: list[tuple[str, float]]) -> EventBundle:
    article = Article(news_id, "t", "c", at.date(), at, "p", "", (), (), "", (), (), ())
    return EventBundle(article, "SEMICONDUCTOR", [CompanyMention(k, s, ("title",)) for k, s in mentions], [],
                       trading_date=at.date())


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=KST)


def test_main_company_is_the_most_salient_then_the_less_covered():
    counts = {SK: 100, NX: 3}
    assert main_company(_bundle("n", at(1, 9), [(SK, 1.0), (NX, 0.8)]), counts) == SK
    assert main_company(_bundle("n", at(1, 9), [(SK, 1.0), (NX, 1.0)]), counts) == NX     # tie → rarer company
    assert main_company(_bundle("n", at(1, 9), []), counts) is None


def test_feed_rotates_companies_within_a_day_and_keeps_days_newest_first():
    bundles = [
        _bundle("sk3", at(25, 15), [(SK, 1.0)]), _bundle("sk2", at(25, 14), [(SK, 1.0)]),
        _bundle("sk1", at(25, 13), [(SK, 1.0)]), _bundle("nx1", at(25, 10), [(NX, 1.0)]),
        _bundle("ss1", at(25, 9), [(SS, 1.0)]), _bundle("ss0", at(25, 8), [(SS, 1.0)]),
        _bundle("hm-yesterday", at(24, 23, 50), [(HM, 1.0)]),
    ]
    times = feed_times(bundles)
    order = [n for n, _ in sorted(times.items(), key=lambda kv: kv[1], reverse=True)]
    # first round: each company's latest news that day (newest first), then second round, ...; the day before comes last
    assert order == ["sk3", "nx1", "ss1", "sk2", "ss0", "sk1", "hm-yesterday"]
    # feed times stay inside their own KST day
    assert all(times[n].astimezone(KST).date() == date(2026, 10, 25) for n in order[:-1])
    assert times["hm-yesterday"].astimezone(KST).date() == date(2026, 10, 24)


def test_main_companies_break_ties_toward_the_less_covered_company():
    bundles = [_bundle("a", at(25, 9), [(SK, 1.0)]), _bundle("b", at(25, 8), [(SK, 1.0)]),
               _bundle("c", at(25, 7), [(SK, 1.0), (NX, 1.0)]), _bundle("d", at(25, 6), [])]
    assert main_companies(bundles) == {"a": SK, "b": SK, "c": NX, "d": None}


def test_news_without_a_company_is_its_own_group():
    bundles = [_bundle("a", at(25, 9), []), _bundle("b", at(25, 8), []), _bundle("c", at(25, 7), [(SK, 1.0)])]
    order = [n for n, _ in sorted(feed_times(bundles).items(), key=lambda kv: kv[1], reverse=True)]
    assert order == ["a", "b", "c"]
