# 실시간 배치에 이어 붙일 것 — AI 파이프라인 인수인계

작성 2026-09-22 · 박준우 (2026-09-23 집계 규칙 정정)

> **읽는 법.** 이 문서의 수치는 적는 시점의 실측입니다. 그 사이에 배포가 바뀔 수
> 있습니다 — 실제로 창(window) 부분이 반나절 만에 두 번 뒤집혔습니다.
> **어긋나면 문서가 아니라 지금 API 응답을 믿으세요.** 그리고 저에게 알려주세요.

지금 실시간 수집·매칭은 잘 돌고 있습니다. 여기에 **네 단계만 이어 붙이면** 뉴스가
화면까지 흐릅니다. 아래 순서대로 읽으시면 됩니다.

---

## 1. 지금 돌고 있는 것

```
cosmos-naver-news@{deliver,search}   10분   네이버 뉴스 수집 (일일 24,000건 한도)
cosmos-news-collector                상시   해외 크롤러
cosmos-news-hdfs-writer              2분    Kafka -> HDFS
cosmos-news-analysis                 1분    기사-기업 매칭 (dict-v1.3)
cosmos-document-loader               2분    새 문서를 Postgres 로
cosmos-dart-daily                    1시간  DART 공시
cosmos-sec-incremental               5분    SEC 증분
```

`cosmos-news-analysis` 가 쓰는 곳:
`/data-lake/analyzed/news/company-mentions/model_version=dict-v1.3/`

**여기서 멈춰 있습니다.** 기업 매칭까지만 하고 감성·관계는 없습니다.

---

## 2. 붙일 단계 — 주기를 둘로 나눠 주세요

| 주기 | 단계 | 쓰는 곳 |
| --- | --- | --- |
| **10분** | 근거 문장 추출 → 감성 → 관련도·영향도 | `public` 에 직접 |
| **하루 1~2회** | 관계 추출 → 점수 → 새 `graph_snapshot` 발행 | `public` 에 직접 |

관계는 10분마다 바뀌지 않습니다. "삼성전자가 SK하이닉스와 경쟁한다"를 10분마다
갱신할 이유가 없습니다. 반대로 기사·감성은 사용자가 바로 느끼는 부분이라 10분이
맞습니다.

---

## 3. 앞쪽 10분 배치는 이미 쉬워졌습니다 (V8 덕분)

V8 마이그레이션이 아래 네 뷰를 `active_run UNION public` 으로 바꿔 놨습니다.

```
current_source_document
current_news_article
current_disclosure
current_company_document     <- 감성·관련도·영향도가 여기 들어갑니다
```

즉 **10분 배치는 `public.company_document` 에 쓰기만 하면 화면에 뜹니다.**
스냅샷을 새로 만들 필요가 없습니다.

다만 **V8 때문에 그런 건 아닙니다.** 백엔드는 애초에 `cosmos_analysis` 를 안 읽고
`public` 을 직접 읽습니다(→ 6번). 결과는 같습니다 — `public` 에 쓰면 뜹니다.

관계 쪽만 다릅니다. `relationship_score_current` 는 **스냅샷 하나**에 묶여 있어서
행을 넣는 것만으로는 안 되고 그 스냅샷에 붙여야 합니다 (→ 5-⑤).

---

## 4. GPU 는 필요 없습니다

측정해 봤습니다.

```
하루 수집량        40~65MB   ≈ 기사 1.2만~1.7만건
  ↓ 기업 매칭 통과율 14.87% (실측)
쌍                 약 2,200건/일
  ↓ 근거 문장 1~2개
문장               약 3,000건/일
  ↓ CPU 11문장/초
소요               약 5분/일
```

10분 주기로 쪼개면 **한 번에 약 2초**입니다. 서버 CPU 로 충분합니다.

GPU(184문장/초)는 **밀린 물량 18만건을 한 번 따라잡을 때만** 필요하고,
그건 제가 로컬에서 별도로 처리합니다.

---

## 5. 반드시 지켜야 할 함정 다섯

### ① FinBERT 라벨 순서가 모델마다 반대입니다

| 모델 | 0 | 1 | 2 |
| --- | --- | --- | --- |
| `snunlp/KR-FinBert-SC` | 부정 | 중립 | 긍정 |
| `ProsusAI/finbert` | **긍정** | 부정 | 중립 |

`argmax` 를 그대로 쓰면 **영어 기사 감성이 통째로 뒤집힙니다.** 호재가 악재가 됩니다.

모델 폴더 `config.json` 의 `id2label` 을 읽어 매핑하세요. 검증 함수가 이미 있습니다 —
`AI/graph/lake_sentiment.py` 의 `label_indexes()` 가 세 클래스인지, `label2id` 와
`id2label` 이 일치하는지 확인하고 아니면 예외를 던집니다.

**리비전 고정 필수.** 허깅페이스에서 같은 이름으로 갱신되면 순서가 또 바뀝니다.

```
snunlp/KR-FinBert-SC   f8586286cc3161fb648e9fee09a456069fd846d0
ProsusAI/finbert       4556d13015211d73dccd3fdd39d39232506f3e43
```

모델 받는 곳은 8절 "감성" 에 적어 뒀습니다. 저장소에는 없고 **하둡 서버** 에 있습니다.

문장에 한글이 있으면 한국어 모델, 없으면 영어 모델로 보냅니다.

### ② 귀속 가드를 직접 만들지 마세요

제가 만들었다가 통과율이 **2.5%** 까지 떨어졌습니다. 공백을 지우는 바람에 `삼성` 이
`삼성전자` 안에서 매칭됐습니다. 기존 함수를 쓰면 **92.6%** 입니다.

```python
from kg_target_sentiment import _surface   # 문장부호 안 지움, 영문 경계만 확인
```

규칙 둘 — **문장 하나**를 그 기업의 근거로 인정할지 판정합니다.

- 문장에 대상 기업명이 없으면 버린다
- **같은 기사에 붙은 다른 기업**이 그 문장에 있으면 버린다 (전체 유니버스가 아니다)

문장 여러 개를 하나로 합치는 건 다음 항목입니다.

### ③ 중립은 **기권**입니다 — 반대표가 아닙니다

> ⚠️ **이 문서의 앞선 판이 여기를 틀리게 적었습니다.**
> "문장마다 라벨이 갈리면 NULL, 다수결로 밀지 않는다" 라고 써뒀는데 그건 초기 버전입니다.
> 실제 구현은 아래와 같이 바꿨고 문서를 안 고쳤습니다. 2026-09-23 정정합니다.

한 (기사, 기업)의 문장 라벨을 하나로 합치는 규칙:

```python
signed = set(labels) - {"NEUTRAL"}      # 중립은 표를 던지지 않는다

if len(signed) == 0:    sentiment = "NEUTRAL"     # 전부 중립
elif len(signed) == 1:  sentiment = signed.pop()  # 긍정만 또는 부정만
else:                   sentiment = None          # 긍정+부정 = 진짜 모순, 버린다
```

Spark 구현은 `AI/graph/spark/attach_sentiment.py` 집계부에 있습니다
(`F.array_remove(F.col("labs"), "NEUTRAL")`).

**왜** — (기사, 기업) **1,167,586쌍**을 문장 라벨 구성으로 세어봤습니다.

```
단일          511,416 (43.8%)   문장 라벨이 하나뿐 — 고민할 게 없다
중립 섞임     453,711 (38.9%)   <- 모순이 아니다. 방향을 채택한다
긍정 vs 부정  202,459 (17.3%)   <- 진짜 모순. 버린다
```

**라벨이 갈린 656,170쌍만 보면 69%가 중립 섞임이고 31%만 진짜 모순입니다.**

#### 실제로 바꿔 봤을 때 (전수 재처리 3b 단계)

만장일치만 인정하던 것을 이 규칙으로 바꾸자:

```
확정 쌍   490,000  ->  872,511      1.77배

긍정  408,225  46.8%
중립  293,742  33.7%
부정  170,544  19.5%
```

**버려지던 38만 쌍이 살아났습니다.** 전부 "사실 서술 + 평가"가 섞여 있던 것들입니다.

> "삼성전자가 SK하이닉스에 HBM을 공급한다"(중립)와
> "3분기 영업이익이 기대치를 상회했다"(긍정)는 서로 반대되는 판단이 아닙니다.
> **중립은 "방향이 없다"이지 "긍정이 아니다"가 아닙니다.**

중립을 반대표로 세면 갈림의 **69%에서 신호를 잃습니다.**

#### 제대로 됐는지 보는 법

총 중립 비율로 판정하지 마세요 — 뉴스 구성에 따라 달라집니다.
**근거 문장이 많아질수록 중립이 떨어지는지**를 보세요.

```sql
WITH n AS (SELECT document_id, company_id, count(*) AS n_sent
           FROM public.document_evidence GROUP BY 1,2)
SELECT LEAST(n.n_sent,5) AS 문장수, count(*) AS 쌍,
       round(100.0*count(*) FILTER (WHERE cd.sentiment='NEUTRAL')/count(*),1) AS 중립
FROM n JOIN public.company_document cd USING (document_id, company_id)
WHERE cd.sentiment IS NOT NULL GROUP BY 1 ORDER BY 1;
```

```
틀렸을 때 (2026-09-22 실측)   1개 46.6% · 2개 41.3% · 3개 46.6% · 4개+ 48.1%   평평
맞을 때   (아카이브 실측)      1개 68%   · 2개 7%    · 5개 3%                   급락
```

문장이 여러 개면 그중 하나쯤은 방향이 있는 게 정상입니다. 평평하거나 오히려
오르면 **중립을 반대표로 세고 있다는 신호**입니다.

### ④ 스팬 오프셋은 `title + "\n" + body` 기준입니다

본문만으로 문장을 자르면 **제목 길이만큼 전부 밀려서** 엉뚱한 문장이 근거로 붙습니다.

### ⑤ 새 스냅샷은 그래프 **전체**를 갈아끼웁니다

백엔드는 스냅샷을 **하나만** 읽습니다 (`GraphQueryRepository.findLatestPublishedSnapshot`).

```sql
SELECT snapshot_id, as_of_at FROM graph_snapshot
WHERE status = 'PUBLISHED' ORDER BY as_of_at DESC, snapshot_id DESC LIMIT 1
```

그리고 간선은 그 `snapshot_id` + `window_type` 으로만 걸러 옵니다. 그래서
**더 늦은 `as_of_at` 으로 스냅샷을 만드는 순간 그 스냅샷에 붙지 않은 관계는 전부
화면에서 사라집니다.** 새로 뽑은 관계만 담아 발행하면 나머지가 통째로 없어집니다.

발행할 때마다 **그 시점의 관계 전량 + 쓰는 창 전량**을 같이 넣어야 합니다.

#### 창은 30D / 1Y / 10Y 입니다 (2026-09-22 배포 `d724b05` 기준)

`MetricWindow` 가 바뀌었습니다. **실측**:

```
30D  200      7D   400
1Y   200      90D  400
10Y  200
```

```java
// BackEnd/.../company/type/MetricWindow.java  (origin/dev)
DAYS_30("30D", Period.ofDays(30)),
YEAR_1("1Y",   Period.ofYears(1)),
YEARS_10("10Y", Period.ofYears(10));
```

**7D·90D 행은 만들어도 서빙되지 않습니다.** 30D / 1Y / 10Y 세 창을 내세요.
(이 문서의 앞선 판이 `7D/30D/90D` 라고 적었습니다. 그 시점엔 맞았고 이후 배포가
뒤집었습니다. 판단 기준은 문서가 아니라 **지금 API 응답**입니다.)

> ⚠️ **지금 배포에 불일치가 있습니다.**
> 프론트는 아직 `7D / 30D / 90D` 버튼을 그립니다
> (`FrontEnd/src/api/types.ts:11`, `RelationshipPanel.tsx:15`, `CompanyPanel.tsx:256`).
> 그래서 화면의 7D·90D 버튼이 **400을 받습니다** — 관계 상세도, 기업 지표도 마찬가지입니다.
> `company_metric_history` 는 7D 341 · 30D 347 · 90D 374 행뿐이라 1Y·10Y 는 200이지만 빕니다.
> 프론트 창 목록을 백엔드에 맞추는 작업이 남아 있습니다.

또 하나 — `relationship_score_current` 의 PK 는 `(relationship_id, window_type)` 이라
**`snapshot_id` 가 안 들어갑니다.** 새 스냅샷으로 upsert 하면 같은 관계의 옛 스냅샷
행이 새 스냅샷으로 끌려옵니다. 되돌리려면 적재 전에 테이블을 떠 두세요.

---

## 6. 백엔드가 읽는 곳은 `public` 입니다 — `cosmos_analysis` 가 아닙니다

**이 문서의 예전 판이 여기를 틀리게 적었습니다.** 실측으로 바로잡습니다.

백엔드는 `cosmos` 롤로 붙고 `search_path` 가 `"$user", public` 입니다.
`GraphQueryRepository` 의 SQL 은 스키마를 하나도 안 붙이므로 실제로 읽는 건 `public.*` 입니다.

```
to_regclass('graph_snapshot')  ->  public.graph_snapshot
```

`cosmos_analysis` 의 `runs` · `active_run` · `current_*` 뷰는 **서빙 경로에 연결돼
있지 않습니다.** 2026-09-20 에 `cosmos_analysis` 로 적재한 관계가 화면에 안 나온
이유가 이것입니다 — 스냅샷을 잘못 고른 게 아니라 아예 다른 스키마였습니다.

**그러니 `active_run` 전환은 하지 마세요. 아무 효과가 없습니다.**
관계·점수·근거는 전부 `public` 에 넣습니다.

본보기: `AI/graph/data/db_full_20260922/load_full_reprocess.sql`
(되돌리기 `rollback_full_reprocess.sql`)

---

## 6-1. 근거 문장을 띄우려면 테이블 다섯 개를 다 채워야 합니다

전수 재처리 때 근거 18,400건을 적재하면서 밟은 지뢰입니다. 실시간 배치에서
**그대로 재현됩니다.**

근거 조회는 이 사슬을 전부 탑니다 (`GraphQueryRepository` 의 `representative_evidence`):

```
relationship_evidence ─┬─> source_document ──> news_article
                       └─> document_evidence ──> company_document   (FK)
```

`relationship_evidence` 만 넣으면 **아무것도 안 보입니다.** 한 군데라도 비면 조용히
빠집니다 — 에러가 안 납니다.

#### ⓐ `document_id` 를 계산하지 말고 DB 에서 찾으세요

수집기 규칙은 `uuid5(NAMESPACE_URL, 'cosmos:document:NEWS:' + sha256(canonical_url))`
입니다(`Crawling/documents/services/document_loader/postgres.py`). 그런데 이 규칙만
믿으면 안 됩니다. 실측으로 근거 문서 8,926건 중 **3,591건이 이미 `news_article` 에
같은 `canonical_url_hash` 로 있으면서 `document_id` 가 달랐습니다.** 예전 적재가
다른 규칙을 썼습니다.

새 id 로 넣으면 `uk_news_article_canonical_url_hash` 에 걸려 `ON CONFLICT DO NOTHING`
으로 **조용히 버려지고**, `JOIN news_article` 에서 빠져 근거가 화면에 안 뜹니다.

```sql
-- 먼저 매핑을 만들고 시작하세요
SELECT d.url_hash, COALESCE(n.document_id, d.fallback_id) AS document_id
FROM 입력 d LEFT JOIN public.news_article n ON n.canonical_url_hash = d.url_hash
```

#### ⓑ `published_at` 을 반드시 채우세요

근거 조회에 `AND sd.published_at IS NOT NULL` 이 걸려 있습니다. 그런데 운영
`source_document` 의 NEWS 438,944건 중 **196,944건(45%)이 발행일이 비어 있습니다.**

이것 때문에 근거 9,188건 중 **5,951건만** 조회됐습니다(관계 1,211개 중 999개).
빈 발행일 3,100건을 채우고 나서야 전량이 나왔습니다.

실시간 수집에서 발행일을 못 뽑으면 그 기사의 근거는 **처음부터 안 보입니다.**

#### ⓒ `content_hash` 를 채우면 나중에 배치가 죽습니다

`document_loader/postgres.py` 에 이 검사가 있습니다.

```
changed news content_hash requires an explicit revision/reanalysis workflow
```

이미 `content_hash` 가 있는 문서를 다른 렌더링으로 다시 수집하면 `LoadError` 로
**배치 전체가 실패합니다.** 아카이브 적재에서는 `content_hash = NULL`,
`status = 'COLLECTED'`, `analysis_version = NULL` 로 둬서 수집기가 정상적으로
넘겨받게 했습니다. 실시간 쪽은 본인이 원본을 쥐고 있으니 채워도 되지만,
**같은 URL 을 두 경로에서 쓰면 이 지뢰를 밟습니다.**

#### ⓓ `company_document` 가 먼저 있어야 합니다

`document_evidence` 의 FK 가 `(document_id, company_id) -> company_document` 입니다.
문서-기업 행 없이 근거 문장만 넣으면 FK 위반으로 막힙니다. 순서:

```
data_source -> source_document -> news_article
            -> company_document -> document_evidence -> relationship_evidence
```

#### ⓔ 제목·작성자가 오염돼 있습니다

원천 파서가 본문을 흘려 넣은 기사가 있습니다. 실측 8,935건 기준:

```
제목에 줄바꿈(본문 유입)   276건   최대 246자
작성자에 줄바꿈            720건   최대 405자
```

`news_article.author` 와 `publisher` 는 **varchar(200)** 이라 그냥 넣으면
`value too long` 으로 **배치가 죽습니다.** 첫 줄만 취하고 길이를 확인하세요.

발행일 표기도 섞여 있습니다 — 8,935건 중 1,047건은 이미 `+00:00` 이 붙어 있고
나머지는 오프셋이 없습니다. 무조건 붙이면 `2026-09-02T01:01:25+00:00+09:00` 같은
값이 나와 `\copy` 가 실패합니다.

#### ⓕ `is_service_visible` — **분석이 끝난 뒤에 `true`**

아카이브 기사는 `false` 로 넣었습니다. 근거 패널은 이 값을 안 보지만 뉴스 목록에는
끼어듭니다.

실시간 수집분도 **수집 즉시 `true` 로 두면 안 됩니다.** 실측:

```
최근 7일 문서-기업  86,412쌍   그 중 감성이 붙은 것 2,287건 (2.6%)
```

지금 감성이 하루 넘게 밀려 있어서(최신 기사 09-22 13:05 · 감성 붙은 마지막 09-21 09:20),
수집 시점에 노출하면 **분석 안 된 기사가 뉴스 목록을 덮습니다.**

`company_document` 에 감성·관련도·영향도를 다 채운 뒤 같은 트랜잭션에서 `true` 로
올리는 쪽이 맞습니다.

*(이 문서의 앞선 판은 "실시간 수집분은 당연히 true" 라고 적었습니다. 그건 제 가정이었고,
"분석이 끝나야 노출" 이라는 요구를 몰랐습니다. 요구 쪽이 맞습니다.)*

본보기: `AI/graph/data/db_evidence_20260922/load_evidence.sql`
(빌더 `AI/graph/build_evidence_load_package.py`, 되돌리기 `rollback_evidence.sql`)

#### ⓖ INVEST 에는 근거 문장이 없습니다

관계 4종 중 INVEST 만 DART 지분 공시에서 나옵니다. 뉴스 문장 경로를 안 타므로
붙일 문장이 없고, 백엔드 근거 조회도 `sd.document_type = 'NEWS'` 로 공시를
거릅니다. **정상입니다** — 화면도 "공시는 점수에 반영되지만 원문 문장은 공개하지
않습니다" 라고 안내합니다.

---

## 7. 참고로 알아둘 것

### 30D 가 얇은 이유 — 이 배치가 채워야 합니다

2026-09-22 전수 재처리 후 실적재된 관계입니다 (`rel-v0.5-full`, 스냅샷 `395a6225…`).

```
30D    106     ← 화면 기본값
1Y     301
10Y  1,251
```

창은 `as_of` 기준 상대 기간인데 **관계 근거의 91%가 1년보다 오래됐습니다**
(10,354건 중 9,432건). 원천이 과거 아카이브 위주고 2025~2026 수집이 얇아서입니다.
그래서 10Y 를 추가했고, 1,251개가 그제서야 살아났습니다.

**실시간 수집분이 관계 추출까지 흘러야 30D 가 채워집니다** — 이 배치가 그 역할입니다.
지금 106개가 실시간 파이프라인이 붙으면 계속 늘어나야 정상입니다.

(적재분에는 7D 40 · 90D 123 행도 들어 있습니다. 백엔드가 `7D/30D/90D` 를 받던 시절
호환용으로 넣은 것인데, 지금은 서빙되지 않는 사문입니다. 새로 만드실 땐 빼세요.)

### 뉴스·공시 슬라이더가 사실상 on/off 인 이유

```
30D 106개 중   뉴스만 66 · 공시만 40 · 양쪽 다 0
90D 123개 중   뉴스만 83 · 공시만 40 · 양쪽 다 0
```

**양쪽 근거를 다 가진 관계가 0개입니다.** 공시 근거가 붙은 40개는 전부 INVEST(DART
지분)이고, PARTNER·SUPPLY·COMPETE 는 전부 뉴스 근거뿐입니다. 그래서 슬라이더가
섞이지 않고 두 덩어리를 켰다 껐다 합니다.

- 공시 100% → INVEST 40개만 남고 나머지 0점
- 뉴스 100% → INVEST 40개가 사라짐

`blendScore` 는 2026-09-22 에 고쳤습니다 — 한쪽만 있는 관계도 슬라이더를 따릅니다
(예전엔 있는 쪽을 그대로 써서 91%가 슬라이더에 반응하지 않았습니다).

섞이게 하려면 PARTNER·SUPPLY·COMPETE 에도 **공시 본문에서 뽑은 근거**가 있어야
합니다. 지금 파이프라인은 지분 신고만 씁니다. 실시간 배치에서 공시 본문 관계추출까지
넣으면 이게 풀립니다.

---

## 8. 참고할 코드 — 단계별

전부 `AI/graph/` 아래입니다. **새로 만들 것은 거의 없고, 이어 붙이는 일입니다.**

### 근거 문장 추출

| 파일 | 역할 |
| --- | --- |
| `relevance_sentence_evidence.py` | 문장 분할 + 문자 오프셋. 원문 손실 없음 |
| `kg_target_sentiment.py` | `_surface()` — 귀속 확인 (감성에서도 씀) |

입력은 매칭 단계가 만든 `spans/` 의 `start`/`end` 입니다.
**오프셋 기준이 `title + 줄바꿈 + body`** 라는 점만 지키면 됩니다.

### 감성

| 파일 | 역할 |
| --- | --- |
| `infer_missing_sentiment.py` | **참고할 본보기.** FinBERT 2종 배치, 언어 분기, 만장일치 규칙 |
| `lake_sentiment.py` | `label_indexes()` — 라벨 순서 검증. 반드시 통과시킬 것 |
| `kg_target_sentiment.py` | `_surface()` — 귀속 가드 |

```bash
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe infer_missing_sentiment.py --limit 2000   # 시험
```

`--limit` 으로 소량 먼저 돌려보시면 됩니다. GPU 가 있으면 자동으로 씁니다.

모델은 **하둡 서버에 있습니다.** `artifacts/` 는 `.gitignore` 라서 저장소를 클론해도
안 따라옵니다. 하둡 서버(`ubuntu@j15c205a.p.ssafy.io`)에 들어가서 받으세요.

```bash
export PATH=$PATH:/opt/hadoop/bin
B=/data-lake/sandbox/news/junwoo/models/news_impact_v2/66c54ef0207b4783/news_impact_v2_bundle/models
hdfs dfs -get $B/sentiment_ko ~/sentiment_models/   # 387MB
hdfs dfs -get $B/sentiment_en ~/sentiment_models/   # 418MB
```

HDFS 는 밖에서 직접 못 붙습니다(DataNode 포트가 안 열려 있음). 서버에서 로컬 디스크로
내린 뒤 `scp` 로 가져가세요.

인터넷이 되면 **HuggingFace 에서 받는 게 빠릅니다.** 같은 파일입니다.

```
sentiment_ko -> snunlp/KR-FinBert-SC
sentiment_en -> ProsusAI/finbert
```

### 관련도 · 영향도

| 파일 | 역할 |
| --- | --- |
| `fill_company_document_scores.py` | `relevance_score` · `impact_score` · `confidence` 계산 |
| `measure_news_salience.py` | 주가 지도학습을 기각한 측정 (왜 규칙인지의 근거) |

```bash
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe fill_company_document_scores.py --emit-sql
```

`--emit-sql` 이 적재용 SQL 을 내줍니다. 공식은 파일 머리말에 있습니다.

### 관계 추출 (하루 1~2회)

| 파일 | 입력 | 산출 |
| --- | --- | --- |
| `extract_relations.py` | 한국어 기사 | SUPPLY · PARTNER · COMPETE |
| `extract_relations_en.py` | 영문 기사 | 같음 (표본 20건 정밀도 95%) |
| `build_grounded_disclosures.py` | DART 공시 | INVEST |
| `spark/extract_relation_candidates.py` | 상류 — 두 기업이 같은 문장에 있는 후보 |

각 파일 머리말에 **실측으로 얻은 규칙과 함정**이 적혀 있습니다. 특히
`extract_relations_en.py` 헤더의 함정 다섯 가지는 네 번 고쳐서 얻은 것입니다.

### 관계 점수 · 신뢰도

| 파일 | 역할 |
| --- | --- |
| `relationship_score_components.py` | **점수 공식의 단일 출처.** `aggregate()` 가 창 전량을 한 번에 낸다 |
| `score_full_reprocess.py` | 그 `aggregate()` 를 쓰는 실행 예시 (창 5개 + INVEST 지분율) |
| `data/relation_evidence/recompute_confidence.sql` | 신뢰도 재계산 |

**`aggregate()` 를 쓰세요.** 예전 배치들이 이 함수를 안 쓰고 `WINDOW = "30D"` 로 직접
만들어서 7D·90D 가 비어 있었습니다.

### 적재 (본보기 — 2026-09-22 실적재본)

| 파일 | 역할 |
| --- | --- |
| `build_full_load_package.py` | 점수 JSON -> 적재 CSV 4종 |
| `data/db_full_20260922/load_full_reprocess.sql` | **관계·점수를 `public` 에 적재** |
| `data/db_full_20260922/rollback_full_reprocess.sql` | 되돌리기 (적재 전 상태를 통째로 백업) |
| `build_evidence_load_package.py` | 근거 CSV 6종. `document_id` 매핑·제목 정제가 여기 있음 |
| `data/db_evidence_20260922/load_evidence.sql` | **근거 문장을 `public` 에 적재** (테이블 5개) |
| `data/db_evidence_20260922/rollback_evidence.sql` | 되돌리기 (채운 발행일도 되돌림) |
| `spark/fetch_evidence_document_meta.py` | 근거 문서의 기사 메타를 원천에서 추출 |
| `spark/fetch_evidence_company_sentiment.py` | 근거 문서의 (기업, 감성) 추출 |

모두 멱등합니다. 여러 번 돌려도 같은 결과입니다.
**적재 전에 `COMMIT;` 을 `ROLLBACK;` 으로 바꿔 한 번 돌려보세요** — 두 SQL 다 마지막에
"넣으려던 수 vs 실제 DB 수" 를 출력합니다. 여기서 안 맞으면 조용히 버려진 행이
있다는 뜻입니다.

예전 판(`data/db_expanded/`, `data/relation_evidence/`)은 `cosmos_analysis` 를 대상으로
합니다. **그쪽은 화면과 연결돼 있지 않습니다** (6절 참조). 본보기로만 보세요.

### 사전 · 매처 (이미 쓰고 계신 것)

| 파일 | 역할 |
| --- | --- |
| `AI/ner/matcher.py` | `CompanyMatcher` · `trim_boilerplate()` · `mask_inserted_headlines()` |
| `AI/ner/data/aliases.csv` | 별칭 1,649개 |
| `AI/ner/data/companies.csv` | 202종목 |
| `AI/ner/spark/batch_mentions.py` | 스냅샷 전수 매칭 (전수 재처리에 쓴 것) |

---

## 9. 예전 결함 — 고쳤습니다. 다시 내지 마세요

예전에는 `relationship_evidence.evidence_id` 가 비어 있었습니다. 화면은 이 값으로
`document_evidence` 의 **근거 문장**을 찾는데 로더가 `document_id` 까지만 연결해서,
"근거 문서 16건" 이라 떠 놓고 문장이 안 보였습니다.

2026-09-22 적재분은 전부 채워져 있습니다.

```
public.relationship_evidence   9,188행 · evidence_id 비어있음 0
```

(`cosmos_analysis.relationship_evidence` 에는 옛 잔재 1,530행 중 1,031행이 아직
비어 있지만, 그 스키마는 서빙과 무관합니다.)

새로 만드실 때 지킬 것: **관계를 만든 바로 그 문장의 `evidence_id` 를 그대로 들고
가세요.** 관계 추출이 문장 단위로 돌아가므로 재추출이 아니라 넘기기만 하면 됩니다.
`build_evidence_load_package.py` 가 `(url_hash, ticker, 문장순서)` 로 `evidence_id` 를
결정적으로 만들어 두 테이블에 같은 값을 씁니다.

**같은 문서의 아무 문장이나 붙이면 관계와 무관한 문장이 노출되므로 그렇게 하면 안 됩니다.**

## 10. 더 자세한 내용

`AI/graph/AI_description_made_by_PARK.MD` — 단계별 모델·데이터·실측 수치 정리.
