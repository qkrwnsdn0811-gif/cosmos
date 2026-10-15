# NEXUS 계획 3: 영향 방향·크기 모델 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 반도체 이벤트의 국내 상장 기업마다 과거 시장 반응으로 학습한 모델로 영향 방향·신뢰도·세기를 계산해 DB에 넣고, 지도와 근거 패널에 보여준다.

**Architecture:** `nexus/nexus/impact/`에 정답(labels)·감성(sentiment)·특징(features)·모델(model)·보고서(report) 모듈을 추가하고 CLI 명령 3개(`sentiment`, `train-impact`, `predict-impact`)로 묶는다. DB는 스키마 변경 없이 V10의 세 컬럼을 채운다. 백엔드는 응답에 `impactMagnitude` 하나를 더하고, 프론트는 노드 모양 결정을 순수 함수로 빼서 지도와 패널이 함께 쓴다.

**Tech Stack:** Python 3.12, pandas 3, scikit-learn ≥ 1.6(`HistGradientBoostingClassifier`, `CalibratedClassifierCV` + `FrozenEstimator`), joblib, 선택 의존성 torch(CPU) + transformers 5.16.1, psycopg 3, pytest / Spring Boot 4.1.1, Java 21 / React 19, TypeScript, Node `node --test`

**Spec:** `docs/superpowers/specs/2026-10-16-nexus-impact-model-design.md`. 입력 계약: 계획 1의 `nexus/data/analyzed/semis/events.pkl`(`list[EventBundle]`), V10 `event_company_impact`.

## Global Constraints

- 파이썬 명령은 저장소 루트에서 `nexus/.venv/Scripts/python -X utf8 ...`로 실행한다. 테스트: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`.
- DB 테스트는 `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres`가 있어야 돌고, 없으면 skip된다. 이 계획의 DB 테스트는 반드시 이 값을 주고 돌린다(`docker compose -f BackEnd/docker-compose.yml up -d`).
- 백엔드 테스트: `cd BackEnd && JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot" DB_URL=jdbc:postgresql://localhost:5432/cosmos_test REDIS_PORT=6380 ./gradlew test`. 개발 DB `cosmos`에는 실데이터가 있어 기존 테스트가 깨진다.
- 프론트 검증: `cd FrontEnd && npm test && npm run typecheck && npm run lint`.
- `torch`와 `transformers`는 `sentiment.py`의 `load_finbert` 안에서만 import한다. `[ml]` 없이 전체 pytest가 돌아야 한다.
- 스펙 고정값(그대로 쓴다):
  - 감성 모델 `snunlp/KR-FinBert-SC`, 리비전 `f8586286cc3161fb648e9fee09a456069fd846d0`, 라벨 0=부정·1=중립·2=긍정, 입력 제목 + 본문 200자, `max_length=256`, `trust_remote_code=False`
  - 분할(이벤트 거래일 t0): 학습 ≤ 2025-12-31, 검증 2026-01-01 ~ 2026-06-30, 시험 ≥ 2026-07-01
  - 정답: σ = t0 앞 60거래일 AR의 표준편차(ddof=1), 값 50개 이상 필요. z = t0의 1일 초과수익률 / σ. z > 0.5 상승, z < −0.5 하락, 나머지 중립
  - 모델: `HistGradientBoostingClassifier(learning_rate=0.05, max_iter=500, early_stopping=True, validation_fraction=0.1, l2_regularization=1.0, random_state=0)`, 검증 구간에서 `CalibratedClassifierCV(FrozenEstimator(...), method="sigmoid")`
  - τ 후보 0.40 ~ 0.90(0.05 간격), 대상 표본 200건 이상, "항상 중립"보다 정확도 0.10 이상 높으면 통과, 통과 후보가 없으면 0.6
  - 주가 조회: 첫 이벤트 150일 전 ~ 오늘(`PriceCache`)
  - 예측한 행의 `model_version`은 `rule-propagation-v1|impact-hgb-v1`, 되돌린 행은 `rule-propagation-v1`
  - 모델 파일 `nexus/data/models/impact-hgb-v1-{chain}.joblib`, 보고서 `docs/nexus/impact-model-report.md`
- V10 마이그레이션 파일을 고치지 않는다. 스키마 변경 없음.
- 화면 문구(그대로):
  - `모델 추정: {상승|하락|중립} · 신뢰도 {NN}% · 세기 {0.00}`
  - `영향 방향 보류 · 신뢰도 {NN}%`
  - `과거 비슷한 기사에 시장이 어떻게 반응했는지로 학습한 추정치이며, 주가 예측이 아닙니다.`
  - 범례: `상승`, `하락`, `중립`, `속 빈 원 방향 보류`, `색 없는 노드: 영향 모델 미적용(해외 상장·판단 보류)`
- 색은 `global.css` 토큰과 새 토큰 `--impact-neutral` 하나만 쓴다. 앱은 다크 전용(`color-scheme: dark`, 테마 전환 없음)이라 `--impact-neutral`은 `#c3cad6` 값 하나만 둔다.
- 커밋은 작업 단위마다 로컬에만 한다. 커밋 메시지에 Claude 표기(Co-Authored-By 등)를 넣지 않는다. push하지 않는다.
- 설치: scikit-learn·joblib은 Task 1에서 venv에 설치한다. torch·transformers 설치와 KR-FinBERT 가중치 다운로드는 Task 9 시작 전에 사용자 승인을 받는다.

## Review Focus

1. **감성 점수가 없는 기사**(감성 분석 뒤에 수집된 기사 등) → 감성 특징은 결측이고 예측은 그대로 나와야 한다. (Task 3, Task 4 테스트)
2. **주가 이력이 없거나 짧은 기업**(신규 상장, 거래 정지) → 정답 없음, 변동성·직전 수익률은 결측, 예측은 나와야 한다. (Task 1, Task 3 테스트)
3. **거래일이 없는 최신 기사**(t0 = None, 주가 달력 밖 날짜) → 정답 없음, 장 마감 후 여부는 평일 규칙, 예측은 나와야 한다. (Task 3 테스트)
4. **한 구간에 특정 클래스가 하나도 없음**(예: 시험 구간에 하락 없음) → 지표 계산이 죽지 않아야 한다. (Task 4 테스트)
5. **오래된 값이 섞인 노드**(HOLD인데 confidence가 남아 있음, magnitude만 null) → HOLD 표시가 우선이고 반지름이 NaN이 되지 않아야 한다. (Task 8 테스트)

---

## 파일 구조

```text
nexus/pyproject.toml                         scikit-learn·joblib 기본 의존성, [ml] 선택 의존성
nexus/nexus/impact/labels.py                 일별 AR, σ, z, 정답, 직전 5일 수익률 (PriceContext)
nexus/nexus/impact/sentiment.py              KR-FinBERT 감싸기, 배치·캐시·이어 돌리기
nexus/nexus/impact/features.py               특징 표 (학습·예측 공용)
nexus/nexus/impact/model.py                  분할, 가중치, 학습·보정, τ, 기준선, 평가, 모델 파일
nexus/nexus/impact/report.py                 검증 보고서 마크다운
nexus/nexus/load/postgres.py                 write_predictions 추가
nexus/nexus/pipeline.py                      sentiment / train-impact / predict-impact 명령
nexus/tests/test_impact_labels.py, test_sentiment.py, test_impact_features.py,
            test_impact_model.py, test_impact_report.py  (+ test_load.py, test_pipeline.py 추가분)
BackEnd/.../event/dto/EventNodeResponse.java, repository/EventQueryRepository.java, service/EventService.java
FrontEnd/src/features/event/nodeStyle.ts     노드 모양·패널 문구 순수 함수
FrontEnd/tests/nodeStyle.test.ts
```

---

### Task 1: 의존성과 정답 계산 (labels)

**Files:**
- Modify: `nexus/pyproject.toml`
- Create: `nexus/nexus/impact/labels.py`
- Test: `nexus/tests/test_impact_labels.py`

**Interfaces:**
- Consumes: `nexus.impact.prices.PriceCache`, `BENCHMARK`, `nexus.models.Company`
- Produces:
  - `daily_abnormal(stock: pd.Series, index: pd.Series) -> pd.Series`: 지수 달력에서 연속한 두 거래일(전날, 당일)의 종목 종가가 모두 있을 때만 AR = (종목 당일/전날 − 1) − (지수 당일/전날 − 1). 인덱스는 `datetime.date`.
  - `class PriceContext(stock: pd.Series, index: pd.Series)`: 속성 `calendar: list[date]`(지수 날짜 정렬), `closes`, `ar`. 메서드는 모두 t0가 달력에 없으면 None.
    - `sigma(t0: date, window: int = 60, min_obs: int = 50) -> float | None`: 달력상 t0 바로 앞 `window`개 거래일의 AR 중 있는 값이 `min_obs`개 이상이면 표준편차(ddof=1). 0이면 None.
    - `day_one(t0) -> float | None`: t0의 AR(= `abnormal_returns(..., windows=(1,))`의 값)
    - `z(t0) -> float | None`: `day_one / sigma`
    - `ret_5d(t0) -> float | None`: `closes[calendar[i0-1]] / closes[calendar[i0-6]] - 1`
  - `label_for(z: float | None, threshold: float = 0.5) -> str | None`: `"POSITIVE" | "NEUTRAL" | "NEGATIVE"`, z가 None이면 None
  - `build_price_contexts(companies: dict[str, Company], prices: PriceCache, start: date, end: date) -> dict[str, PriceContext]`: KOSPI·KOSDAQ 기업만, 지수는 `BENCHMARK[company.market]`. 종가가 비면 넣지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_impact_labels.py`)

```python
import statistics
from datetime import date, timedelta

import pandas as pd
import pytest

from nexus.impact.abnormal import abnormal_returns
from nexus.impact.labels import PriceContext, build_price_contexts, daily_abnormal, label_for
from nexus.impact.prices import PriceCache
from nexus.models import Company


def from_returns(returns, start=date(2025, 1, 1)):
    days = [start + timedelta(days=i) for i in range(len(returns) + 1)]
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    return pd.Series(closes, index=days), pd.Series(1000.0, index=days), days


def test_daily_abnormal_subtracts_index():
    d = [date(2026, 1, i) for i in (1, 2, 3)]
    ar = daily_abnormal(pd.Series([100.0, 110.0, 99.0], index=d), pd.Series([1000.0, 1000.0, 1100.0], index=d))
    assert list(ar.index) == d[1:]
    assert ar[d[1]] == pytest.approx(0.10)
    assert ar[d[2]] == pytest.approx(-0.20)


def test_halted_day_removes_that_day_and_the_next():
    d = [date(2026, 1, i) for i in (1, 2, 3, 4)]
    stock = pd.Series([100.0, 101.0, 102.0], index=[d[0], d[2], d[3]])
    assert list(daily_abnormal(stock, pd.Series(1000.0, index=d)).index) == [d[3]]


def test_sigma_z_and_label():
    returns = [0.01 if i % 2 else -0.01 for i in range(61)]
    stock, index, days = from_returns(returns + [0.03])
    ctx, t0 = PriceContext(stock, index), days[-1]
    sigma = statistics.stdev(returns[-60:])
    assert ctx.sigma(t0) == pytest.approx(sigma)
    assert ctx.day_one(t0) == pytest.approx(0.03)
    assert ctx.z(t0) == pytest.approx(0.03 / sigma)
    assert label_for(ctx.z(t0)) == "POSITIVE"


def test_sigma_needs_fifty_observations():
    stock, index, days = from_returns([0.01, -0.01] * 22 + [0.02])   # 45 returns
    assert PriceContext(stock, index).sigma(days[-1]) is None
    assert PriceContext(stock, index).z(days[-1]) is None


def test_label_boundaries():
    assert [label_for(z) for z in (0.51, 0.5, -0.5, -0.51, None)] == ["POSITIVE", "NEUTRAL", "NEUTRAL", "NEGATIVE", None]


def test_ret_5d_uses_closes_before_t0():
    days = [date(2026, 2, 1) + timedelta(days=i) for i in range(12)]
    stock = pd.Series([100.0 + i for i in range(12)], index=days)
    ctx = PriceContext(stock, pd.Series(1000.0, index=days))
    assert ctx.ret_5d(days[10]) == pytest.approx(109.0 / 104.0 - 1)
    assert ctx.ret_5d(days[3]) is None


def test_day_one_matches_existing_abnormal_returns():
    stock, index, days = from_returns([0.01, -0.02, 0.03])
    index = index * pd.Series([1.0, 1.01, 1.0, 0.98], index=days)
    expected = abnormal_returns("K", days[2], stock, index, "KOSPI", windows=(1,))[0].abnormal_return
    assert PriceContext(stock, index).day_one(days[2]) == pytest.approx(expected)


def test_unknown_t0_gives_none():
    stock, index, _ = from_returns([0.01] * 70)
    ctx = PriceContext(stock, index)
    assert (ctx.sigma(date(2030, 1, 1)), ctx.day_one(date(2030, 1, 1)), ctx.ret_5d(date(2030, 1, 1))) == (None, None, None)


def test_build_price_contexts_skips_nasdaq_and_uses_market_index(tmp_path):
    days = pd.to_datetime([date(2026, 1, 1) + timedelta(days=i) for i in range(5)])
    calls = []

    def fetch(symbol, start, end):
        calls.append(symbol)
        return pd.DataFrame({"Close": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=days)

    companies = {c.key: c for c in (Company("KOSPI:1", "KOSPI", "1", "가", "SEMICONDUCTOR", "제조"),
                                    Company("KOSDAQ:2", "KOSDAQ", "2", "나", "SEMICONDUCTOR", "장비"),
                                    Company("NASDAQ:NV", "NASDAQ", "NV", "다", "SEMICONDUCTOR", "설계"))}
    cache = PriceCache(tmp_path, fetch=fetch, today=lambda: date(2026, 2, 1))
    ctx = build_price_contexts(companies, cache, date(2026, 1, 1), date(2026, 1, 31))
    assert set(ctx) == {"KOSPI:1", "KOSDAQ:2"}
    assert {"KS11", "KQ11", "1", "2"} <= set(calls) and "NV" not in calls
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_labels.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.impact.labels`)

- [ ] **Step 3: 의존성 추가와 설치**

`nexus/pyproject.toml`의 `dependencies`에 `"scikit-learn>=1.6"`, `"joblib"`을 추가하고, `[project.optional-dependencies]`에 `ml = ["torch", "transformers==5.16.1", "tokenizers==0.23.2", "huggingface-hub==1.30.0", "safetensors==0.8.0"]`를 추가한다(COSMOS `AI/gpu_news/requirements.txt`와 같은 버전).

Run: `nexus/.venv/Scripts/python -m pip install -e "nexus[dev]"`
Expected: scikit-learn 1.6 이상, joblib 설치. torch는 설치되지 않는다.

- [ ] **Step 4: `labels.py` 구현** (위 Interfaces 그대로)

- [ ] **Step 5: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_labels.py -q`
Expected: 9 passed

- [ ] **Step 6: 커밋**

```bash
git add nexus/pyproject.toml nexus/nexus/impact/labels.py nexus/tests/test_impact_labels.py
git commit -m "feat: 영향 모델 정답 계산 (평소 변동폭 대비 1일 초과수익률)"
```

---

### Task 2: 감성 분석 (sentiment)과 `sentiment` 명령

**Files:**
- Create: `nexus/nexus/impact/sentiment.py`
- Modify: `nexus/nexus/pipeline.py` (`cmd_sentiment`, 파서)
- Test: `nexus/tests/test_sentiment.py`

**Interfaces:**
- Consumes: `nexus.models.Article`, `EventBundle`
- Produces:
  - `MODEL_ID = "snunlp/KR-FinBert-SC"`, `MODEL_REVISION = "f8586286cc3161fb648e9fee09a456069fd846d0"`, `COLUMNS = ["news_id", "p_neg", "p_neu", "p_pos", "model_revision"]`
  - `Scorer = Callable[[list[str]], list[tuple[float, float, float]]]`: 입력 텍스트 순서대로 (p_neg, p_neu, p_pos)
  - `article_text(article: Article) -> str`: `f"{article.title}\n{article.content}".strip()`
  - `to_neg_neu_pos(probs: Sequence[float], id2label: dict[int, str]) -> tuple[float, float, float]`: `id2label` 이름(소문자)이 negative·neutral·positive 세 개면 그 이름대로 재배열하고, 아니면 KR-FinBert-SC 문서 순서(0=부정, 1=중립, 2=긍정)로 본다.
  - `load_finbert(threads: int = 8) -> Scorer`: 함수 안에서 torch·transformers를 import. `torch.set_num_threads(threads)`, `AutoTokenizer`/`AutoModelForSequenceClassification.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False)`, `eval()`, `torch.no_grad()`, `truncation=True, max_length=256, padding=True`, softmax 후 `to_neg_neu_pos(row, model.config.id2label)`. 단위 테스트 없음(Task 9에서 실제 실행).
  - `score_articles(articles: Iterable[Article], scorer: Scorer, out_path: Path, *, batch_size: int = 32, save_every: int = 50, revision: str = MODEL_REVISION) -> pd.DataFrame`: 기존 파일에서 같은 `revision`인 행은 남기고 그 news_id는 건너뛴다. news_id 중복은 한 번만 점수 낸다. `save_every` 배치마다 (기존 + 새 행)을 파일에 쓰고, 끝나면 한 번 더 쓴다. 반환은 파일 전체 내용.
  - `read_sentiment(path: Path, revision: str = MODEL_REVISION) -> pd.DataFrame`: `news_id` 인덱스, `p_neg/p_neu/p_pos` 컬럼. 파일이 없으면 빈 프레임.
  - CLI `sentiment --chain semis [--threads 8] [--batch-size 32]`: `events.pkl`의 기사를 `analyzed/{chain}/sentiment.parquet`로 점수 낸다. 진행 상황을 `save_every`마다 출력.

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_sentiment.py`)

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_sentiment.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.impact.sentiment`)

- [ ] **Step 3: `sentiment.py`와 `cmd_sentiment` 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_sentiment.py -q && nexus/.venv/Scripts/python -X utf8 -m nexus sentiment --help`
Expected: 7 passed, 도움말에 `--chain`, `--threads`, `--batch-size`

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/impact/sentiment.py nexus/nexus/pipeline.py nexus/tests/test_sentiment.py
git commit -m "feat: KR-FinBERT 기사 감성 분석 명령 (배치·캐시·이어 돌리기)"
```

---

### Task 3: 입력 특징 (features)

**Files:**
- Create: `nexus/nexus/impact/features.py`
- Test: `nexus/tests/test_impact_features.py`

**Interfaces:**
- Consumes: Task 1 `PriceContext`, `label_for`; `nexus.relations.activation.active_relations`; `nexus.models.*`
- Produces:
  - 범주 값: `REL_VALUES = ("NONE", "SUPPLY", "PARTNER", "COMPETE", "INVEST", "CO_MENTIONED")`, `SOURCE_VALUES = ("NONE", "NEWS", "COSMOS", "COMENTION")`, `ROLE_VALUES = ("설계", "제조", "장비", "소재", "부품", "후공정", "고객")`, `MARKET_VALUES = ("KOSPI", "KOSDAQ")`, `CATEGORICAL = {"first_rel": REL_VALUES, "last_rel": REL_VALUES, "edge_source": SOURCE_VALUES, "role": ROLE_VALUES, "market": MARKET_VALUES}`
  - `BASE_FEATURES = ["sent_neg", "sent_neu", "sent_pos", "has_incident", "rel_supply", "rel_partner", "rel_compete", "rel_invest", "n_reported", "after_close", "is_inferred", "hop", "score", "in_title", "in_content", "in_orgs", "first_rel", "last_rel", "path_sign", "edge_source", "role", "market", "buzz_ratio", "ret_5d", "vol_60"]`
  - `META_COLUMNS = ["news_id", "company_key", "basis", "trading_date", "z", "label"]`
  - `feature_columns(vocab: list[str]) -> list[str]`: `BASE_FEATURES + [f"cat:{c}" for c in vocab]`
  - `path_sign(path: tuple[PathStep, ...]) -> int`: 빈 경로 +1. SUPPLY·PARTNER·INVEST +1, COMPETE −1, CO_MENTIONED 0의 곱
  - `after_close(dateline: datetime, calendar: set[date], last_day: date | None) -> int`: KST 15:30 이후면 1. 출고일(KST)이 `last_day` 이하이고 달력에 없으면 1. `last_day`보다 뒤(또는 None)면 토·일만 1. 나머지 0
  - `class MentionTimeline(bundles: list[EventBundle])`: `buzz_ratio(key: str, at: datetime) -> float` = c24 / (c30 / 30 + 1). c24 = [at − 24시간, at) 동안 그 기업을 `mentions`에 가진 기사 수, c30 = [at − 30일, at) 동안의 수. 기사 자신(같은 시각)과 이후 기사는 세지 않는다.
  - `category_vocab(bundles: list[EventBundle], min_share: float = 0.01) -> list[str]`: 기사 비율이 `min_share` 이상인 빅카인즈 분류를 정렬해 반환
  - `build_frame(bundles: list[EventBundle], companies: dict[str, Company], sentiment: pd.DataFrame, prices: dict[str, PriceContext], vocab: list[str], timeline: MentionTimeline, calendar: list[date]) -> pd.DataFrame`: `status == "OK"`이고 시장이 KOSPI·KOSDAQ인 영향 행마다 한 줄. 컬럼은 `META_COLUMNS + feature_columns(vocab)`. 범주형 컬럼은 `CATEGORICAL`의 고정 범주를 가진 pandas Categorical(목록 밖 값은 결측), 나머지 특징은 float. 감성·주가·t0가 없으면 해당 값은 NaN, `z`·`label`은 결측(pandas 3은 문자열 컬럼의 None을 NaN으로 바꿀 수 있으니 판정은 `pd.isna`로 한다).
    - `n_reported`: 그 이벤트의 REPORTED 영향 수. `in_title/in_content/in_orgs`: REPORTED면 그 기업 `CompanyMention.sources`의 `title`/`content`/`orgs`, INFERRED면 0
    - `first_rel/last_rel/edge_source`: 경로 첫·마지막 단계의 `rel`, 첫 단계 `source`. 경로가 없으면 `"NONE"`
    - `vol_60` = `ctx.sigma(t0)`, `ret_5d` = `ctx.ret_5d(t0)`, `z` = `ctx.z(t0)`, `label` = `label_for(z)`

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_impact_features.py`)

```python
from datetime import date, datetime, timedelta, timezone

import math
import pandas as pd
import pytest

from nexus.impact.features import (META_COLUMNS, MentionTimeline, after_close, build_frame, category_vocab,
                                   feature_columns, path_sign)
from nexus.impact.labels import PriceContext
from nexus.models import Article, Company, CompanyMention, EventBundle, Impact, PathStep

KST = timezone(timedelta(hours=9))
A = Company("KOSPI:1", "KOSPI", "1", "가전자", "SEMICONDUCTOR", "제조")
B = Company("KOSDAQ:2", "KOSDAQ", "2", "나장비", "SEMICONDUCTOR", "장비")
C = Company("KOSDAQ:3", "KOSDAQ", "3", "다소재", "SEMICONDUCTOR", "소재")
D = Company("KOSDAQ:4", "KOSDAQ", "4", "라부품", "SEMICONDUCTOR", "부품")
N = Company("NASDAQ:NV", "NASDAQ", "NV", "엔비", "SEMICONDUCTOR", "설계")
COMPANIES = {c.key: c for c in (A, B, C, D, N)}


def art(news_id, at, title="제목", content="본문", categories=("경제>반도체",), incidents=()):
    return Article(news_id, title, content, at.date(), at, "테스트일보", "", categories, incidents, "", (), (), ())


def bundle(news_id="E1", at=datetime(2026, 7, 2, 10, 0, tzinfo=KST), t0=date(2026, 7, 2)):
    mentions = [CompanyMention(A.key, 1.0, ("title",)), CompanyMention(B.key, 0.8, ("content",)),
                CompanyMention(N.key, 0.6, ("orgs",))]
    impacts = [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None),
               Impact(B.key, "REPORTED", 0, (), 0.8, "OK", None),
               Impact(N.key, "REPORTED", 0, (), 0.6, "OK", None),
               Impact(C.key, "INFERRED", 1, (PathStep(C.key, B.key, "COMPETE", "COSMOS", 1.0),), 0.4, "OK", None),
               Impact(D.key, "INFERRED", 1, (PathStep(D.key, A.key, "SUPPLY", "NEWS", 0.5),), 0.1, "HOLD", "전이 강도 낮음")]
    return EventBundle(art(news_id, at, title="가전자 공급 계약"), "SEMICONDUCTOR", mentions, impacts, [], t0)


def frame_for(bundles, prices=None, sentiment=None, calendar=None):
    sentiment = sentiment if sentiment is not None else pd.DataFrame(
        {"p_neg": [0.1], "p_neu": [0.2], "p_pos": [0.7]}, index=pd.Index(["E1"], name="news_id"))
    calendar = calendar or [date(2026, 7, 1), date(2026, 7, 2)]
    return build_frame(bundles, COMPANIES, sentiment, prices or {}, ["경제>반도체"], MentionTimeline(bundles), calendar)


def test_path_sign():
    s = lambda rel: PathStep("x", "y", rel, "COSMOS", 1.0)
    assert path_sign(()) == 1
    assert path_sign((s("SUPPLY"),)) == 1
    assert path_sign((s("COMPETE"),)) == -1
    assert path_sign((s("COMPETE"), s("COMPETE"))) == 1
    assert path_sign((s("SUPPLY"), s("CO_MENTIONED"))) == 0


def test_after_close():
    cal = {date(2026, 7, 2), date(2026, 7, 3)}      # 7/4 토, 7/5 일
    at = lambda d, h, m=0: datetime(2026, 7, d, h, m, tzinfo=KST)
    assert after_close(at(2, 15, 29), cal, date(2026, 7, 3)) == 0
    assert after_close(at(2, 15, 30), cal, date(2026, 7, 3)) == 1
    assert after_close(at(1, 10), cal, date(2026, 7, 3)) == 1          # 달력 안인데 거래일 아님
    assert after_close(at(6, 10), cal, date(2026, 7, 3)) == 0          # 달력 밖 월요일
    assert after_close(at(5, 10), cal, date(2026, 7, 3)) == 1          # 달력 밖 일요일


def test_buzz_ratio_counts_only_before_the_article():
    t = datetime(2026, 7, 20, 12, 0, tzinfo=KST)
    times = [t - timedelta(hours=1), t - timedelta(hours=2), t - timedelta(days=10), t, t + timedelta(hours=1)]
    bundles = [EventBundle(art(f"B{i}", x), "SEMICONDUCTOR", [CompanyMention(A.key, 1.0, ("title",))], [], [], None)
               for i, x in enumerate(times)]
    assert MentionTimeline(bundles).buzz_ratio(A.key, t) == pytest.approx(2 / (3 / 30 + 1))


def test_category_vocab_keeps_frequent_categories():
    at = datetime(2025, 3, 1, 9, tzinfo=KST)
    bundles = [EventBundle(art(f"V{i}", at, categories=("경제>반도체",) + (("IT>보안",) if i == 0 else ())),
                           "SEMICONDUCTOR", [], [], [], None) for i in range(200)]
    assert category_vocab(bundles) == ["경제>반도체"]


def test_rows_columns_and_values():
    raw = frame_for([bundle()])
    assert list(raw.columns) == META_COLUMNS + feature_columns(["경제>반도체"])
    f = raw.set_index("company_key")
    assert set(f.index) == {A.key, B.key, C.key}                       # HOLD·NASDAQ 제외
    assert (f.loc[A.key, "in_title"], f.loc[A.key, "path_sign"], f.loc[A.key, "first_rel"]) == (1, 1, "NONE")
    assert (f.loc[C.key, "is_inferred"], f.loc[C.key, "path_sign"], f.loc[C.key, "edge_source"]) == (1, -1, "COSMOS")
    assert f.loc[C.key, "in_title"] == 0
    assert f.loc[A.key, "n_reported"] == 3 and f.loc[A.key, "rel_supply"] == 1
    assert f.loc[A.key, "sent_pos"] == pytest.approx(0.7) and f.loc[A.key, "cat:경제>반도체"] == 1
    assert isinstance(f["role"].dtype, pd.CategoricalDtype)


def test_missing_sentiment_prices_and_trading_day_give_nan():
    empty = pd.DataFrame(columns=["p_neg", "p_neu", "p_pos"], index=pd.Index([], name="news_id"))
    f = frame_for([bundle(t0=None)], sentiment=empty)
    assert len(f) == 3
    assert f["sent_pos"].isna().all() and f["vol_60"].isna().all() and f["ret_5d"].isna().all()
    assert f["label"].isna().all() and f["z"].isna().all()


def test_label_comes_from_price_context():
    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(124)]
    returns = [0.01 if i % 2 else -0.01 for i in range(122)] + [0.05]
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    ctx = PriceContext(pd.Series(closes, index=days), pd.Series(1000.0, index=days))
    b = bundle(t0=days[-1])
    f = frame_for([b], prices={A.key: ctx}, calendar=days).set_index("company_key")
    assert f.loc[A.key, "label"] == "POSITIVE"
    assert not math.isnan(f.loc[A.key, "vol_60"]) and pd.isna(f.loc[B.key, "label"])
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_features.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.impact.features`)

- [ ] **Step 3: `features.py` 구현** (위 Interfaces 그대로. `MentionTimeline`은 기업별 정렬된 UTC 초 목록과 `bisect`로 센다)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_features.py -q`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/impact/features.py nexus/tests/test_impact_features.py
git commit -m "feat: 영향 모델 입력 특징 (감성·관계 경로·언급 위치·기사 급증·주가 맥락)"
```

---

### Task 4: 모델 (학습·보정·τ·기준선·평가)

**Files:**
- Create: `nexus/nexus/impact/model.py`
- Test: `nexus/tests/test_impact_model.py`

**Interfaces:**
- Consumes: Task 3 `feature_columns`, `CATEGORICAL`, `META_COLUMNS`
- Produces:
  - 상수: `CLASSES = ("NEGATIVE", "NEUTRAL", "POSITIVE")`, `TRAIN_END = date(2025, 12, 31)`, `VALID_END = date(2026, 6, 30)`, `TAU_GRID = tuple(round(0.40 + 0.05 * i, 2) for i in range(11))`, `TAU_MIN_SAMPLES = 200`, `TAU_MARGIN = 0.10`, `TAU_FALLBACK = 0.6`, `MODEL_VERSION = "impact-hgb-v1"`, `SENTIMENT_COLUMNS = ["sent_neg", "sent_neu", "sent_pos"]`, `METHODS = ("항상 중립", "규칙", "감성만", "전체 모델")`
  - `split(frame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`: `label`이 있는 행만, `trading_date` 기준 학습·검증·시험
  - `sample_weights(frame) -> np.ndarray`: 1 / (`company_key`, `trading_date`) 그룹 크기
  - `fit_calibrated(train, valid, columns: list[str]) -> CalibratedClassifierCV`: 스펙 하이퍼파라미터로 학습(`sample_weight`), 검증 구간 시그모이드 보정(`sample_weight`)
  - `predict_proba(model, frame, columns) -> np.ndarray`: (n, 3), `CLASSES` 순서. 모델에 없는 클래스 열은 0
  - `choose_tau(proba, y, weights) -> tuple[float, bool]`: `TAU_GRID`를 작은 값부터 보며, 신뢰도 ≥ τ인 표본이 `TAU_MIN_SAMPLES`개 이상이고 그 표본의 가중 정확도 − 가중 중립 비율 ≥ `TAU_MARGIN`이면 `(τ, True)`. 없으면 `(TAU_FALLBACK, False)`
  - `outputs(proba, tau) -> list[tuple[str | None, float, float]]`: (방향 또는 None, 신뢰도 = 최대 확률, 세기 = |p_POSITIVE − p_NEGATIVE|), 소수 여섯째 자리 반올림
  - `rule_baseline(frame) -> np.ndarray`: 감성 최대 확률 클래스(부정 −1, 중립 0, 긍정 +1, 결측 0) × `path_sign` → +1 POSITIVE, −1 NEGATIVE, 0 NEUTRAL
  - `scores(y_true, y_pred, weights, proba=None) -> dict[str, float | None]`: `accuracy`, `macro_f1`(`labels=CLASSES`, `zero_division=0`), `log_loss`(`labels=CLASSES`, proba가 None이면 None)
  - `@dataclass Evaluation`: `generated_at: datetime`, `data_start: date`, `data_end: date`, `sentiment_revision: str`, `tau: float`, `tau_passed: bool`, `split_counts: dict[str, dict[str, float]]`(`"학습"/"검증"/"시험"` → `{"n": 행 수, "NEGATIVE": 가중 비율, "NEUTRAL": …, "POSITIVE": …}`), `comparison: dict[str, dict]`(`METHODS` → `scores` 결과), `by_basis: dict[str, dict]`, `by_first_rel: dict[str, dict]`(전체 모델 argmax 기준 `scores`, 표본 수 `n` 포함), `coverage: float`(시험 구간 가중 비율), `covered_accuracy: float | None`, `calibration: list[tuple[str, int, float, float]]`(구간 `"0.3–0.4"` … `"0.9–1.0"`, 표본 수, 평균 신뢰도, 실제 정확도. 빈 구간은 뺀다), `importance: list[tuple[str, float]]`(시험 구간 최대 20,000행 표본에 `permutation_importance(scoring="neg_log_loss", n_repeats=3, random_state=0)`, log-loss 증가량 상위 10개)
  - `@dataclass ImpactModel`: `estimator`, `columns: list[str]`, `vocab: list[str]`, `tau: float`, `tau_passed: bool`, `save(path: Path) -> None`(joblib, 상위 폴더 생성), `@staticmethod load(path: Path) -> ImpactModel`
  - `train_and_evaluate(frame, vocab: list[str], *, sentiment_revision: str, now: datetime) -> tuple[ImpactModel, Evaluation]`: 분할 → 전체 모델 학습·보정 → 검증 구간에서 τ 선택 → 시험 구간에서 네 방법 비교와 나머지 항목 계산. "감성만"은 `SENTIMENT_COLUMNS`로 같은 `fit_calibrated`

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_impact_model.py`)

```python
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from nexus.impact.features import CATEGORICAL, META_COLUMNS, feature_columns
from nexus.impact.model import (METHODS, ImpactModel, choose_tau, outputs, predict_proba, rule_baseline,
                                sample_weights, scores, split, train_and_evaluate)

VOCAB = ["경제>반도체"]


def synthetic_frame(n=3000, seed=0):
    """감성(긍정−부정) × 경로 부호에 신호를 심은 데이터. 2025-01 ~ 2026-09에 고르게 퍼뜨린다."""
    rng = np.random.default_rng(seed)
    pos = rng.uniform(0, 1, n)
    neg = rng.uniform(0, 1 - pos)
    sign = rng.choice([1, -1], n, p=[0.8, 0.2])
    signal = (pos - neg) * sign + rng.normal(0, 0.15, n)
    label = np.where(signal > 0.25, "POSITIVE", np.where(signal < -0.25, "NEGATIVE", "NEUTRAL"))
    days = [date(2025, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 630, n)]
    f = pd.DataFrame({c: 0.0 for c in feature_columns(VOCAB)}, index=range(n))
    f["sent_pos"], f["sent_neg"], f["sent_neu"], f["path_sign"] = pos, neg, 1 - pos - neg, sign
    f["is_inferred"] = (sign == -1).astype(float)
    for col, values in CATEGORICAL.items():
        f[col] = pd.Categorical([values[0]] * n, categories=list(values))
    f["news_id"] = [f"N{i}" for i in range(n)]
    f["company_key"] = [f"KOSPI:{i % 50}" for i in range(n)]
    f["basis"] = np.where(sign == -1, "INFERRED", "REPORTED")
    f["trading_date"], f["z"], f["label"] = days, signal, label
    return f[META_COLUMNS + feature_columns(VOCAB)]


def test_split_by_trading_date():
    train, valid, test = split(synthetic_frame())
    assert train["trading_date"].max() <= date(2025, 12, 31)
    assert valid["trading_date"].min() >= date(2026, 1, 1) and valid["trading_date"].max() <= date(2026, 6, 30)
    assert test["trading_date"].min() >= date(2026, 7, 1)


def test_sample_weights_one_over_group_size():
    f = pd.DataFrame({"company_key": ["A", "A", "A", "B"], "trading_date": [date(2026, 1, 2)] * 3 + [date(2026, 1, 2)]})
    assert list(sample_weights(f)) == pytest.approx([1 / 3, 1 / 3, 1 / 3, 1.0])


def test_model_beats_always_neutral_on_signal():
    model, ev = train_and_evaluate(synthetic_frame(), VOCAB, sentiment_revision="r", now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    full, neutral = ev.comparison["전체 모델"], ev.comparison["항상 중립"]
    assert set(ev.comparison) == set(METHODS)
    assert full["macro_f1"] > neutral["macro_f1"] + 0.2 and full["accuracy"] > neutral["accuracy"]
    assert neutral["log_loss"] is None and full["log_loss"] is not None
    assert model.tau in [round(0.40 + 0.05 * i, 2) for i in range(11)]
    assert set(ev.split_counts) == {"학습", "검증", "시험"}


def test_choose_tau_picks_smallest_passing_value():
    wrong = np.tile([0.30, 0.25, 0.45], (300, 1))                       # 신뢰도 0.45, 상승 예측, 정답 중립
    right = np.tile([0.10, 0.10, 0.80], (300, 1))                       # 신뢰도 0.80, 상승 예측, 정답 상승
    y = ["NEUTRAL"] * 300 + ["POSITIVE"] * 300
    assert choose_tau(np.vstack([wrong, right]), y, np.ones(600)) == (0.5, True)


def test_choose_tau_needs_200_samples_and_falls_back():
    right = np.tile([0.10, 0.10, 0.80], (150, 1))
    assert choose_tau(right, ["POSITIVE"] * 150, np.ones(150)) == (0.6, False)
    neutral = np.tile([0.10, 0.80, 0.10], (500, 1))
    assert choose_tau(neutral, ["NEUTRAL"] * 500, np.ones(500)) == (0.6, False)


def test_outputs_hide_direction_below_tau():
    assert outputs(np.array([[0.2, 0.3, 0.5], [0.1, 0.1, 0.8]]), 0.6) == [(None, 0.5, 0.3), ("POSITIVE", 0.8, 0.7)]


def test_rule_baseline():
    f = pd.DataFrame({"sent_neg": [0.8, 0.1, 0.1, np.nan], "sent_neu": [0.1, 0.1, 0.8, np.nan],
                      "sent_pos": [0.1, 0.8, 0.1, np.nan], "path_sign": [-1, 1, 1, 1]})
    assert list(rule_baseline(f)) == ["POSITIVE", "POSITIVE", "NEUTRAL", "NEUTRAL"]


def test_scores_use_weights_and_survive_missing_class():
    assert scores(["POSITIVE", "NEGATIVE"], ["POSITIVE", "POSITIVE"], [3, 1])["accuracy"] == pytest.approx(0.75)
    s = scores(["NEUTRAL", "POSITIVE"], ["NEUTRAL", "NEUTRAL"], [1, 1], proba=np.array([[0.1, 0.8, 0.1], [0.1, 0.6, 0.3]]))
    assert np.isfinite(s["macro_f1"]) and np.isfinite(s["log_loss"])


def test_save_and_load_give_same_predictions(tmp_path):
    frame = synthetic_frame()
    model, _ = train_and_evaluate(frame, VOCAB, sentiment_revision="r", now=datetime(2026, 10, 16, tzinfo=timezone.utc))
    model.save(tmp_path / "m" / "model.joblib")
    loaded = ImpactModel.load(tmp_path / "m" / "model.joblib")
    np.testing.assert_allclose(predict_proba(loaded.estimator, frame, loaded.columns),
                               predict_proba(model.estimator, frame, model.columns))
    assert (loaded.tau, loaded.vocab) == (model.tau, model.vocab)
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_model.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.impact.model`)

- [ ] **Step 3: `model.py` 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_model.py -q`
Expected: 9 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/impact/model.py nexus/tests/test_impact_model.py
git commit -m "feat: 영향 모델 학습·보정·방향 보류 기준·기준선 평가"
```

---

### Task 5: 검증 보고서 (report)

**Files:**
- Create: `nexus/nexus/impact/report.py`
- Test: `nexus/tests/test_impact_report.py`

**Interfaces:**
- Consumes: Task 4 `Evaluation`, `METHODS`
- Produces:
  - `render_report(ev: Evaluation) -> str`, `write_report(ev: Evaluation, path: Path) -> None`(상위 폴더 생성, UTF-8)
  - 제목과 절은 고정: `# NEXUS 영향 모델 검증 보고서`, `## 1. 실행 정보`, `## 2. 표본`, `## 3. 시험 구간 비교`, `## 4. 나눠 보기`, `## 5. 표시 기준(τ)`, `## 6. 보정표`, `## 7. 중요한 입력`, `## 8. 한계`
  - τ 줄: 통과면 `τ = 0.55 (검증 구간에서 항상 중립보다 10%p 이상 나은 가장 작은 값)`, 아니면 `τ = 0.60 (기준 미달: 항상 중립보다 10%p 이상 나은 기준을 찾지 못해 기본값을 씀)`
  - 비교표 형식: `| 방법 | 정확도 | macro-F1 | log-loss |`, 수치는 소수 셋째 자리, None은 `-`. 비율은 `%` 소수 첫째 자리
  - 한계 절: 스펙 §9의 "관계 그래프의 미래 정보", "학습 구간 이벤트의 표시값", "같은 날 여러 기사", "기사 감성은 기업별이 아님", "일별 주가 잡음" 다섯 항목을 한 줄씩

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_impact_report.py`)

```python
from datetime import date, datetime, timezone

from nexus.impact.model import Evaluation
from nexus.impact.report import render_report, write_report

SCORES = {"accuracy": 0.612, "macro_f1": 0.455, "log_loss": 0.931}


def evaluation(passed=True):
    return Evaluation(
        generated_at=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc), data_start=date(2025, 1, 2),
        data_end=date(2026, 10, 13), sentiment_revision="f858", tau=0.55 if passed else 0.6, tau_passed=passed,
        split_counts={k: {"n": 100, "NEGATIVE": 0.2, "NEUTRAL": 0.6, "POSITIVE": 0.2} for k in ("학습", "검증", "시험")},
        comparison={"항상 중립": {"accuracy": 0.5, "macro_f1": 0.222, "log_loss": None},
                    "규칙": {"accuracy": 0.52, "macro_f1": 0.3, "log_loss": None},
                    "감성만": {"accuracy": 0.55, "macro_f1": 0.35, "log_loss": 0.99}, "전체 모델": SCORES},
        by_basis={"REPORTED": {**SCORES, "n": 70}, "INFERRED": {**SCORES, "n": 30}},
        by_first_rel={"NONE": {**SCORES, "n": 70}, "SUPPLY": {**SCORES, "n": 30}},
        coverage=0.42, covered_accuracy=0.71,
        calibration=[("0.4–0.5", 40, 0.45, 0.43), ("0.5–0.6", 30, 0.55, 0.57)],
        importance=[("sent_pos", 0.031), ("path_sign", 0.012)])


def test_report_has_all_sections_and_table():
    text = render_report(evaluation())
    for head in ("# NEXUS 영향 모델 검증 보고서", "## 1. 실행 정보", "## 3. 시험 구간 비교", "## 6. 보정표", "## 8. 한계"):
        assert head in text
    assert "| 전체 모델 | 0.612 | 0.455 | 0.931 |" in text
    assert "| 항상 중립 | 0.500 | 0.222 | - |" in text
    assert "τ = 0.55" in text and "42.0%" in text and "sent_pos" in text


def test_report_marks_fallback_tau():
    assert "기준 미달" in render_report(evaluation(passed=False))


def test_write_report_creates_file(tmp_path):
    path = tmp_path / "docs" / "report.md"
    write_report(evaluation(), path)
    assert path.read_text(encoding="utf-8").startswith("# NEXUS 영향 모델 검증 보고서")
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_report.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.impact.report`)

- [ ] **Step 3: `report.py` 구현**

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_impact_report.py -q`
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/impact/report.py nexus/tests/test_impact_report.py
git commit -m "feat: 영향 모델 검증 보고서 생성"
```

---

### Task 6: DB 반영과 `train-impact`·`predict-impact` 명령

**Files:**
- Modify: `nexus/nexus/load/postgres.py`, `nexus/nexus/pipeline.py`
- Test: `nexus/tests/test_load.py`(추가), `nexus/tests/test_pipeline.py`(추가)

**Interfaces:**
- Consumes: Task 1 `build_price_contexts`, Task 2 `read_sentiment`, `MODEL_REVISION`, Task 3 `MentionTimeline`, `category_vocab`, `build_frame`, Task 4 `ImpactModel`, `train_and_evaluate`, `predict_proba`, `outputs`, `TRAIN_END`, Task 5 `write_report`, 기존 `ensure_companies`, `event_id`
- Produces:
  - `postgres.IMPACT_MODEL_VERSION = "rule-propagation-v1|impact-hgb-v1"`
  - `write_predictions(conn, value_chain: str, rows: list[tuple[str, UUID, str | None, float, float]]) -> int`: rows = (news_id, company_id, 방향, 신뢰도, 세기). 한 트랜잭션에서 ① 그 밸류체인 이벤트의 모든 영향 행을 세 컬럼 NULL, `model_version = 'rule-propagation-v1'`로 되돌리고 ② 임시 테이블(`ON COMMIT DROP`)에 `COPY`로 넣은 뒤 ③ `UPDATE ... FROM` 임시 테이블로 `status = 'OK'`인 행만 갱신하고 `model_version = IMPACT_MODEL_VERSION`. 반환은 ③에서 갱신된 행 수
  - `pipeline.PRICE_HISTORY = timedelta(days=150)`
  - `pipeline.impact_dataset(bundles: list[EventBundle], companies: dict[str, Company], sentiment: pd.DataFrame, prices: PriceCache, vocab: list[str] | None, *, today: date) -> tuple[pd.DataFrame, list[str]]`: `vocab`이 None이면 `trading_date ≤ TRAIN_END`인 이벤트로 `category_vocab`. 주가는 (가장 이른 출고일 − `PRICE_HISTORY`) ~ `today`, 달력은 KOSPI 지수 날짜
  - CLI `train-impact --chain semis`: 모델을 `nexus/data/models/impact-hgb-v1-{chain}.joblib`에, 보고서를 `docs/nexus/impact-model-report.md`에 쓰고 τ와 시험 구간 비교표를 출력
  - CLI `predict-impact --chain semis --database-url URL`: 모델을 읽어 같은 `vocab`으로 특징을 만들고 `write_predictions`. 방향별 개수(방향 보류 포함)를 출력

- [ ] **Step 1: 실패하는 테스트 작성**

`nexus/tests/test_load.py`에 추가한다. 기존 `bundle()`에 HOLD 행이 없으니 이 테스트용 묶음을 따로 만든다.

```python
from nexus.load.postgres import IMPACT_MODEL_VERSION, write_predictions

D = Company("KOSDAQ:900004", "KOSDAQ", "900004", "숲부품", "SEMICONDUCTOR", "부품")


def bundle_with_hold(synthetic_docs):
    b = bundle(synthetic_docs)
    b.impacts.append(Impact(D.key, "INFERRED", 1, (PathStep(D.key, A.key, "SUPPLY", "COSMOS", 1.0),), 0.1, "HOLD", "전이 강도 낮음"))
    return b


def impact_row(conn, company_id):
    return conn.execute("SELECT impact_direction, impact_magnitude, confidence, model_version FROM event_company_impact "
                        "WHERE company_id = %s", (company_id,)).fetchone()


def test_write_predictions_sets_values_and_version(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C, D])
    load_events(pg, [bundle_with_hold(synthetic_docs)], ids)
    news = "SYN.20261013100000001"
    n = write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31), (news, ids[B.key], None, 0.41, 0.12),
                                                (news, ids[D.key], "NEGATIVE", 0.9, 0.8)])
    assert n == 2                                                      # HOLD 행은 갱신하지 않는다
    assert impact_row(pg, ids[A.key]) == ("POSITIVE", Decimal("0.310000"), Decimal("0.620000"), IMPACT_MODEL_VERSION)
    assert impact_row(pg, ids[B.key])[:3] == (None, Decimal("0.120000"), Decimal("0.410000"))
    assert impact_row(pg, ids[D.key]) == (None, None, None, "rule-propagation-v1")


def test_write_predictions_is_idempotent_and_resets_old_values(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C, D])
    load_events(pg, [bundle_with_hold(synthetic_docs)], ids)
    news = "SYN.20261013100000001"
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31), (news, ids[B.key], "NEGATIVE", 0.7, 0.5)])
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31)])
    write_predictions(pg, "SEMICONDUCTOR", [(news, ids[A.key], "POSITIVE", 0.62, 0.31)])
    assert impact_row(pg, ids[A.key])[0] == "POSITIVE"
    assert impact_row(pg, ids[B.key]) == (None, None, None, "rule-propagation-v1")
```

(`from decimal import Decimal`을 파일 상단 import에 추가한다.)

`nexus/tests/test_pipeline.py`에 추가한다.

```python
from datetime import datetime, timedelta, timezone

import pytest

from nexus.impact.features import META_COLUMNS, feature_columns
from nexus.models import CompanyMention, EventBundle, Impact
from nexus.pipeline import impact_dataset, main


def test_impact_dataset_builds_vocab_from_training_events(synthetic_docs, tmp_path):
    kospi = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
    days = pd.to_datetime([date(2025, 6, 1) + timedelta(days=i) for i in range(300)])
    cache = PriceCache(tmp_path, fetch=lambda s, a, b: pd.DataFrame({"Close": [100.0 + i for i in range(300)]}, index=days),
                       today=lambda: date(2026, 4, 1))
    article = to_article(synthetic_docs[0])
    old = EventBundle(replace(article, news_id="OLD", categories=("경제>반도체",),
                              dateline=datetime(2025, 11, 3, 10, tzinfo=timezone(timedelta(hours=9)))),
                      "SEMICONDUCTOR", [CompanyMention(kospi.key, 1.0, ("title",))],
                      [Impact(kospi.key, "REPORTED", 0, (), 1.0, "OK", None)], [], date(2025, 11, 3))
    new = replace(old, article=replace(old.article, news_id="NEW", categories=("경제>자동차",)), trading_date=date(2026, 2, 2))
    frame, vocab = impact_dataset([old, new], {kospi.key: kospi}, pd.DataFrame(columns=["p_neg", "p_neu", "p_pos"]),
                                  cache, None, today=date(2026, 4, 1))
    assert vocab == ["경제>반도체"]
    assert list(frame.columns) == META_COLUMNS + feature_columns(vocab) and len(frame) == 2


@pytest.mark.parametrize("command", ["sentiment", "train-impact", "predict-impact"])
def test_impact_commands_are_registered(command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
```

(`from dataclasses import replace`가 없으면 추가한다.)

- [ ] **Step 2: 실패 확인**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_load.py nexus/tests/test_pipeline.py -q`
Expected: FAIL (`ImportError: write_predictions`, `impact_dataset`)

- [ ] **Step 3: `write_predictions`, `impact_dataset`, `cmd_train_impact`, `cmd_predict_impact`, 파서 구현**

- [ ] **Step 4: 통과 확인 (전체 파이썬 테스트)**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`
Expected: 전부 통과, skip 0

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/load/postgres.py nexus/nexus/pipeline.py nexus/tests/test_load.py nexus/tests/test_pipeline.py
git commit -m "feat: 영향 모델 학습·예측 명령과 DB 반영 (스키마 변경 없음, 재실행 시 같은 결과)"
```

---

### Task 7: 백엔드 `impactMagnitude`

**Files:**
- Modify: `BackEnd/src/main/java/com/cosmos/api/event/dto/EventNodeResponse.java`, `event/repository/EventQueryRepository.java`(`findImpacts` SELECT, `ImpactRow`), `event/service/EventService.java`, `BackEnd/src/test/java/com/cosmos/api/event/controller/EventControllerTest.java`(생성자 인자), `docs/api-specification.md`
- Test: `BackEnd/src/test/java/com/cosmos/api/event/service/EventServiceIntegrationTest.java`

**Interfaces:**
- Produces: `EventNodeResponse(..., String impactDirection, BigDecimal impactMagnitude, BigDecimal confidence, ...)`(`impactDirection` 바로 다음), `ImpactRow`도 같은 위치에 `BigDecimal impactMagnitude`. JSON 필드명 `impactMagnitude`.

- [ ] **Step 1: 실패하는 테스트 작성** (`EventServiceIntegrationTest`에 추가)

```java
@Test
void impactModelFieldsPassThrough() {
    jdbcTemplate.update("UPDATE event_company_impact SET impact_direction = 'POSITIVE', impact_magnitude = 0.31, "
            + "confidence = 0.62 WHERE event_id = ? AND company_id = ?", E1, C1);

    EventMapResponse map = eventService.findEventMap(E1);

    EventNodeResponse c1 = node(map, C1);
    assertThat(c1.impactDirection()).isEqualTo("POSITIVE");
    assertThat(c1.impactMagnitude()).isEqualByComparingTo("0.31");
    assertThat(c1.confidence()).isEqualByComparingTo("0.62");
    EventNodeResponse c2 = node(map, C2);
    assertThat(c2.impactDirection()).isNull();
    assertThat(c2.impactMagnitude()).isNull();
    assertThat(c2.confidence()).isNull();
}
```

- [ ] **Step 2: 실패 확인**

Run: `cd BackEnd && JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot" DB_URL=jdbc:postgresql://localhost:5432/cosmos_test REDIS_PORT=6380 ./gradlew test --tests 'com.cosmos.api.event.*'`
Expected: 컴파일 실패(`impactMagnitude()` 없음)

- [ ] **Step 3: 구현**

DTO·`ImpactRow`·SELECT(`i.impact_magnitude`)·`EventService` 생성자 호출·`EventControllerTest` 생성자 인자(`null` 추가)를 고친다. `EventNodeResponse` 주석은 "impactDirection·impactMagnitude·confidence는 영향 모델(nexus predict-impact)이 채운다. confidence가 있는데 impactDirection이 null이면 방향 보류다."로 바꾼다. `docs/api-specification.md` 지도 응답 예시의 `"impactDirection": null,` 다음 줄에 `"impactMagnitude": null,`을 넣고, 마지막 설명 줄을 다음으로 바꾼다.

```markdown
- `impactDirection`(POSITIVE·NEUTRAL·NEGATIVE), `impactMagnitude`(0~1, 상승 확률과 하락 확률의 차이), `confidence`(0~1, 가장 큰 클래스의 확률)는 영향 모델(`nexus predict-impact`)이 채운다. 국내 상장사이면서 `status`가 `OK`인 노드만 값이 있다. `confidence`가 있는데 `impactDirection`이 null이면 신뢰도가 표시 기준 미만인 '방향 보류'다. 해외 상장사와 `HOLD` 노드는 셋 다 null이다.
```

- [ ] **Step 4: 통과 확인 (전체 백엔드)**

Run: `cd BackEnd && JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot" DB_URL=jdbc:postgresql://localhost:5432/cosmos_test REDIS_PORT=6380 ./gradlew test`
Expected: BUILD SUCCESSFUL, 212개 통과

- [ ] **Step 5: 커밋**

```bash
git add BackEnd/src docs/api-specification.md
git commit -m "feat: 이벤트 지도 응답에 영향 세기(impactMagnitude) 추가"
```

---

### Task 8: 프론트 노드 모양·범례·근거 패널

**Files:**
- Create: `FrontEnd/src/features/event/nodeStyle.ts`, `FrontEnd/tests/nodeStyle.test.ts`
- Modify: `FrontEnd/src/api/types.ts`(`EventNode.impactMagnitude`), `FrontEnd/src/features/event/EventRadialMap.tsx`, `FrontEnd/src/features/event/EventEvidencePanel.tsx`, `FrontEnd/src/features/event/events.css`

**Interfaces:**
- Produces (`nodeStyle.ts`, import 없는 순수 함수 — `radialLayout.ts`처럼 타입도 파일 안에 둔다):
  - `interface NodeStyleInput { basis: "REPORTED" | "INFERRED"; status: "OK" | "HOLD"; score: number | null; impactDirection: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null; impactMagnitude: number | null; confidence: number | null }`
  - `interface NodeStyle { fill: string; fillOpacity: number; stroke: string | null; radius: number; impactLabel: string | null }`
  - `modelRan(n) -> boolean`: `status === "OK" && confidence !== null`
  - `nodeStyle(n) -> NodeStyle`:

    | 상태 | fill | fillOpacity | stroke | radius | impactLabel |
    |---|---|---|---|---|---|
    | HOLD | `var(--text-4)` | 0.6 | null | 근거 10 + 8s, 추정 8 + 10s (s = score ?? 0.5) | null |
    | 모델 미적용 | 근거 `var(--primary)`, 추정 `var(--accent)` | 1 | null | 위와 같음 | null |
    | 상승·하락·중립 | `var(--pos)` / `var(--neg)` / `var(--impact-neutral)` | 1 | null | 9 + 12 × (impactMagnitude ?? 0) | `상승 추정` / `하락 추정` / `중립 추정` |
    | 방향 보류 | `var(--panel-solid)` | 1 | 근거 `var(--primary)`, 추정 `var(--accent)` | 9 + 12 × (impactMagnitude ?? 0) | `방향 보류` |

  - `impactLine(n) -> string | null`: 모델이 돌고 방향이 있으면 `모델 추정: 상승 · 신뢰도 62% · 세기 0.31`(신뢰도 `Math.round(c × 100)`, 세기 `toFixed(2)`), 방향이 없으면 `영향 방향 보류 · 신뢰도 41%`, 모델이 안 돌았으면 null
  - `IMPACT_DISCLAIMER = "과거 비슷한 기사에 시장이 어떻게 반응했는지로 학습한 추정치이며, 주가 예측이 아닙니다."`

- [ ] **Step 1: 실패하는 테스트 작성** (`FrontEnd/tests/nodeStyle.test.ts`)

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { impactLine, nodeStyle, type NodeStyleInput } from "../src/features/event/nodeStyle.ts";

const base: NodeStyleInput = { basis: "INFERRED", status: "OK", score: 0.5, impactDirection: null, impactMagnitude: null, confidence: null };

test("HOLD wins over stale model values", () => {
  const n = { ...base, status: "HOLD" as const, impactDirection: "POSITIVE" as const, impactMagnitude: 0.9, confidence: 0.8 };
  const s = nodeStyle(n);
  assert.equal(s.fill, "var(--text-4)");
  assert.equal(s.fillOpacity, 0.6);
  assert.equal(s.impactLabel, null);
  assert.equal(impactLine(n), null);
});

test("nodes without the model keep basis colors and the old radius", () => {
  assert.deepEqual(nodeStyle({ ...base, basis: "REPORTED", score: 1 }), { fill: "var(--primary)", fillOpacity: 1, stroke: null, radius: 18, impactLabel: null });
  assert.equal(nodeStyle({ ...base, score: null }).radius, 13);
  assert.equal(nodeStyle(base).fill, "var(--accent)");
});

test("directions get colors and magnitude-based radius", () => {
  const up = nodeStyle({ ...base, impactDirection: "POSITIVE", impactMagnitude: 0.5, confidence: 0.7 });
  assert.equal(up.fill, "var(--pos)");
  assert.equal(up.radius, 15);
  assert.equal(up.impactLabel, "상승 추정");
  assert.equal(nodeStyle({ ...base, impactDirection: "NEGATIVE", impactMagnitude: 0, confidence: 0.7 }).fill, "var(--neg)");
  assert.equal(nodeStyle({ ...base, impactDirection: "NEUTRAL", impactMagnitude: 0, confidence: 0.7 }).fill, "var(--impact-neutral)");
});

test("held direction is a hollow circle with the basis color ring", () => {
  const s = nodeStyle({ ...base, impactMagnitude: 0.25, confidence: 0.41 });
  assert.deepEqual(s, { fill: "var(--panel-solid)", fillOpacity: 1, stroke: "var(--accent)", radius: 12, impactLabel: "방향 보류" });
  assert.equal(nodeStyle({ ...base, basis: "REPORTED", confidence: 0.41 }).stroke, "var(--primary)");
});

test("missing magnitude with a confidence does not produce NaN", () => {
  assert.equal(nodeStyle({ ...base, confidence: 0.41 }).radius, 9);
});

test("panel line", () => {
  assert.equal(impactLine({ ...base, impactDirection: "POSITIVE", impactMagnitude: 0.31, confidence: 0.62 }), "모델 추정: 상승 · 신뢰도 62% · 세기 0.31");
  assert.equal(impactLine({ ...base, impactMagnitude: 0.12, confidence: 0.41 }), "영향 방향 보류 · 신뢰도 41%");
  assert.equal(impactLine(base), null);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd FrontEnd && npm test`
Expected: FAIL (`nodeStyle.ts`를 찾을 수 없음), 기존 12개는 통과

- [ ] **Step 3: 구현**
  - `nodeStyle.ts`: 위 Interfaces 그대로.
  - `types.ts`: `EventNode`에 `impactMagnitude: number | null`을 `impactDirection` 다음에 추가하고, 주석을 "영향 모델(nexus predict-impact)이 채운다. confidence가 있는데 impactDirection이 null이면 방향 보류"로 바꾼다.
  - `EventRadialMap.tsx`: 노드 원은 `nodeStyle(n)`의 `fill`·`fillOpacity`·`stroke ?? "transparent"`·`radius`를 쓴다(`strokeWidth` 2.5). 선택 표시는 `selected`일 때 반지름 `radius + 4`, `fill="none"`, `stroke="var(--text)"`, `strokeWidth` 2인 원을 따로 그린다. 이름표 위치 계산도 `radius`를 쓴다. 선 색(`tone`)과 점선 규칙은 그대로 둔다. `title`·`aria-label` 끝에 ` · {impactLabel}`을 붙인다(값이 있을 때만). 범례에 `<i className="dot pos" /> 상승`, `<i className="dot neg" /> 하락`, `<i className="dot neu" /> 중립`, `<i className="dot hollow" /> 속 빈 원 방향 보류`, `<li className="meta">색 없는 노드: 영향 모델 미적용(해외 상장·판단 보류)</li>`를 추가한다. 1단계 주석("영향 방향 모델이 없다")은 지운다.
  - `EventEvidencePanel.tsx`: 근거·추정 두 분기 모두 배지 바로 아래에 `impactLine(node)`가 있으면 `<p className="event-impact"><b>{line}</b></p><p className="meta">{IMPACT_DISCLAIMER}</p>`를 넣는다.
  - `events.css`: `:root { --impact-neutral: #c3cad6; }`, `.event-map-legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }`, `.dot.pos { background: var(--pos); }`, `.dot.neg { background: var(--neg); }`, `.dot.neu { background: var(--impact-neutral); }`, `.dot.hollow { border: 2px solid var(--primary); }`, `.event-impact { margin: 10px 0 4px; font-size: 13px; }`.

- [ ] **Step 4: 통과 확인**

Run: `cd FrontEnd && npm test && npm run typecheck && npm run lint`
Expected: 18개 통과, 타입·린트 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add FrontEnd/src FrontEnd/tests/nodeStyle.test.ts
git commit -m "feat: 지도에 영향 방향 표시 (상승·하락·중립 색, 방향 보류는 속 빈 원, 패널 문구)"
```

---

### Task 9: 실제 데이터 실행과 확인

**Files:**
- Modify: `nexus/pyproject.toml`(설치된 torch 버전 고정), `docs/nexus/slice1-run.md`(§6 추가)
- Create (명령이 생성): `docs/nexus/impact-model-report.md`

- [ ] **Step 1: 사용자 승인 받기 (여기서 멈춘다)**

다음을 알리고 승인을 받는다.
- `nexus/.venv/Scripts/python -m pip install -e "nexus[ml]"`: PyPI에서 torch CPU 버전(약 200MB)과 transformers 5.16.1 등 설치
- 첫 감성 실행 때 Hugging Face 공식 저장소에서 `snunlp/KR-FinBert-SC`(리비전 고정) 가중치 약 400MB 다운로드

- [ ] **Step 2: 설치 확인**

Run: `nexus/.venv/Scripts/python -m pip install -e "nexus[ml]" && nexus/.venv/Scripts/python -c "import torch, transformers; print(torch.__version__, torch.cuda.is_available(), transformers.__version__)"`
Expected: `<torch 버전> False 5.16.1`. 출력된 torch 버전으로 `pyproject.toml`의 `[ml]`을 `torch==<버전>`으로 고정한다.

- [ ] **Step 3: 감성 분석 (백그라운드, 약 1시간)**

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus sentiment --chain semis > nexus/data/sentiment.log 2>&1`
Expected: 마지막 줄에 점수 낸 기사 수. `sentiment.parquet` 행 수 = `events.pkl`의 고유 news_id 수(약 129,864)

- [ ] **Step 4: 학습**

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus train-impact --chain semis`
Expected: τ와 시험 구간 비교표 출력, `docs/nexus/impact-model-report.md` 생성. 모델이 기준선보다 낫지 않아도 멈추지 않고 그대로 기록한다.

- [ ] **Step 5: 예측과 DB 반영**

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus predict-impact --chain semis --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos`
Expected: 방향별 개수 출력. 확인 SQL:
- `SELECT impact_direction, count(*) FROM event_company_impact WHERE confidence IS NOT NULL GROUP BY 1` → 상승·하락·중립·NULL(방향 보류) 분포
- `SELECT count(*) FROM event_company_impact i JOIN company c USING (company_id) WHERE (i.status = 'HOLD' OR c.market = 'NASDAQ') AND i.confidence IS NOT NULL` → 0

- [ ] **Step 6: 화면 확인**

실행 중인 bootRun이 있으면 먼저 멈춘다. 백엔드(`REDIS_PORT=6380 SERVER_PORT=18081`)와 프론트 개발 서버를 띄우고 `/events`에서 시험 구간(2026-07 이후) 이벤트를 고른다. 확인 항목:
- 상승·하락·중립 색과 속 빈 원
- 범례와 근거 패널 문구
- 선택 원이 속 빈 원 테두리와 겹치지 않는지
- 모바일 폭(375px)

- [ ] **Step 7: 기록과 커밋**

`docs/nexus/slice1-run.md`에 `## 6. 영향 모델 실행 (날짜)` 절을 추가한다. 내용은 단계별 실행 시간, 표본 수, τ, 시험 구간 핵심 수치(전체 모델 vs 항상 중립), 방향 분포, 보고서 링크다.

```bash
git add nexus/pyproject.toml docs/nexus/impact-model-report.md docs/nexus/slice1-run.md
git commit -m "docs: 영향 모델 실제 실행 결과와 검증 보고서"
```
