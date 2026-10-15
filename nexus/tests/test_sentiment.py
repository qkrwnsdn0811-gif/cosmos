from dataclasses import replace

import pandas as pd
import pytest

from nexus.impact.sentiment import article_text, read_sentiment, score_articles, to_neg_neu_pos
from nexus.ingest.normalize import to_article


class FakeScorer:
    def __init__(self, fail_on_call=None):
        self.batches, self.fail_on_call = [], fail_on_call

    def __call__(self, texts):
        self.batches.append(len(texts))
        if self.fail_on_call == len(self.batches):
            raise RuntimeError("boom")
        return [(0.1, 0.2, 0.7) for _ in texts]


@pytest.fixture
def articles(synthetic_docs):
    base = to_article(synthetic_docs[0])
    return [replace(base, news_id=f"N{i}") for i in range(5)]


def test_label_order_by_name():
    assert to_neg_neu_pos([0.7, 0.2, 0.1], {0: "positive", 1: "negative", 2: "neutral"}) == (0.2, 0.1, 0.7)


def test_label_order_falls_back_to_documented_order():
    assert to_neg_neu_pos([0.6, 0.3, 0.1], {0: "LABEL_0", 1: "LABEL_1", 2: "LABEL_2"}) == (0.6, 0.3, 0.1)


def test_article_text_joins_title_and_content(articles):
    a = articles[0]
    assert article_text(a) == f"{a.title}\n{a.content}".strip()


def test_scores_in_batches_and_writes_parquet(articles, tmp_path):
    scorer, out = FakeScorer(), tmp_path / "s.parquet"
    frame = score_articles(articles, scorer, out, batch_size=2)
    assert scorer.batches == [2, 2, 1]
    assert sorted(pd.read_parquet(out)["news_id"]) == [f"N{i}" for i in range(5)]
    assert set(frame["model_revision"]) == {"f8586286cc3161fb648e9fee09a456069fd846d0"}


def test_skips_scored_articles_unless_revision_changes(articles, tmp_path):
    out = tmp_path / "s.parquet"
    score_articles(articles, FakeScorer(), out)
    again = FakeScorer()
    score_articles(articles, again, out)
    assert again.batches == []
    other = FakeScorer()
    score_articles(articles, other, out, revision="other")
    assert sum(other.batches) == 5


def test_duplicate_news_ids_scored_once(articles, tmp_path):
    scorer = FakeScorer()
    score_articles(articles + articles[:2], scorer, tmp_path / "s.parquet")
    assert sum(scorer.batches) == 5


def test_partial_save_lets_a_rerun_resume(articles, tmp_path):
    out = tmp_path / "s.parquet"
    with pytest.raises(RuntimeError):
        score_articles(articles, FakeScorer(fail_on_call=3), out, batch_size=2, save_every=1)
    assert len(pd.read_parquet(out)) == 4
    rest = FakeScorer()
    score_articles(articles, rest, out, batch_size=2)
    assert rest.batches == [1]
    assert list(read_sentiment(out).columns) == ["p_neg", "p_neu", "p_pos"]
