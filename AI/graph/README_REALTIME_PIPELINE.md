# 실시간 뉴스 → Postgres 적재 파이프라인

최신 뉴스가 들어왔을 때 화면에 뜨기까지 무엇을 거치는지 정리한다.
2026-09-21 기준 실측이며, 추정한 값은 없다.

## 지금 어디까지 도나

```
[1] 수집          동작       public.source_document        403,369건, 실시간
[2] 본문 정제     없음       hdfs_clean_uri 전부 NULL      ← 최대 선행조건
[3] 기업 추출     반쪽       사전만 동작, NER·링커 미연결
[4] 관련성 분류   없음       B 분류기 미연결
[5] 근거 검사     없음
[6] 감성          없음       배치 없음
[7] 관련도·영향도 없음       배치 없음
[8] 관계 추출     없음       배치 없음
[9] 스냅샷 발행   없음       화면은 2026-09-15 에 멈춤
```

**1·3만 돌고 나머지는 멈춰 있다.** 최근 3일 기사 35,446건 중 32,079건이 기업 연결까지는
됐지만 감성·영향도·관계는 전부 NULL 이다.

---

## 단계별 계약

### [1] 수집 — 이미 동작

```
출처 → public.source_document + public.news_article
       document_id, title, original_url, published_at, hdfs_raw_uri
```

본문은 DB 가 아니라 **HDFS** 에 들어간다.

### [2] 본문 정제 — 없음. 이게 막히면 뒤가 전부 막힌다

```
HDFS raw → 보일러플레이트 제거 → HDFS clean → hdfs_clean_uri 채우기
```

최근 7일 44,042건 기준 `hdfs_clean_uri` 가 **0건**이다.

주의: `trim_boilerplate` 를 제대로 해야 한다. 과거 사이드바 오염으로 오탐이 대량
발생한 적이 있다 (APR 제거 18,097건 중 97.8% 가 `[뉴스핌 베스트 기사]` 때문).

### [3] 기업 추출 — 사전만 동작

```python
from pipeline import CompanyExtractor
ex = CompanyExtractor(aliases="data/aliases.csv", model="models/ner-company-v2")
out = ex.extract(news_id, title, body)
```

| 구성 | 역할 | 상태 |
| --- | --- | --- |
| 사전 `ner/data/aliases.csv` | 티커를 **직접** 준다. 정확하지만 등록된 표기만 | 사용 중 |
| NER `ner/models/ner-company-v2/` | 새 표기를 찾는다. **스팬만** 주고 티커는 모른다 | **미연결** |
| 링커 `ner/linker.py` | NER 스팬 → 티커. 못 하면 `unlinked` 로 남긴다 | **미연결** |

NER 단독 성능(test F1 0.9874)을 그대로 인용하면 안 된다. `train_summary.json` 에
`"distant supervision labels"` 라고 적혀 있다 — **사전이 만든 라벨로 학습**했으므로
그 숫자는 "사전을 얼마나 잘 흉내내는가"이지 추출 정확도가 아니다.

출력 → `public.company_document` (document_id, company_id)

### [4] 관련성 분류 — B 분류기, 미연결

```
artifacts/kg_cascade_v2_pairfix_20260917_v1/student_B/attempt_001/
```

기사–기업 쌍이 경제적으로 관련 있는지 거른다. 이게 없으면 이름만 스쳐 지나간
기사까지 전부 관계·감성 계산에 들어간다.

### [5] 근거 검사 — 규칙

기업이 등장한 **문장과 위치**를 원문에서 확인한다. 모델 없음.

주의: 스팬 오프셋은 `title + "\n" + body` 기준이다. 본문만으로 문장을 자르면 전부 어긋난다.

출력 → `document_evidence` (sentence_text, sentence_order)

### [6] 감성 — FinBERT 2종

| 언어 | 모델 | 리비전 |
| --- | --- | --- |
| 한국어 | `snunlp/KR-FinBert-SC` | `f8586286cc3161fb648e9fee09a456069fd846d0` |
| 영어 | `ProsusAI/finbert` | `4556d13015211d73dccd3fdd39d39232506f3e43` |

번들 안에 이미 있다: `artifacts/news_impact_v2_bundle/models/sentiment_{ko,en}/`

**리비전 고정 필수.** 두 모델의 라벨 순서가 다르다 (`0=negative` vs `0=positive`).
`lake_sentiment.label_indexes` 를 거치지 않고 argmax 를 쓰면 긍정·부정이 뒤집힌다.

**귀속 가드 필수.** 직접 만들었다가 통과율이 2.5% 까지 떨어졌다.

```python
from kg_target_sentiment import _surface   # 문장부호 안 지움, 영문 경계만 확인
```

- 문장에 대상 기업명이 없으면 버린다
- **같은 기사에 붙은 다른 기업**이 그 문장에 있으면 버린다 (전체 유니버스가 아니다)
- 한 기업에 대해 문장마다 라벨이 갈리면 NULL 이다. 다수결로 밀지 않는다

출력 → `company_document.sentiment`

### [7] 관련도·영향도 — 규칙. 모델이 아니다

```
relevance_score = 0.25 + 0.55·depth + 0.20·share
    depth = (min(근거문장수, 4) − 1) / 3
    share = 1 / 기사내기업수
impact_score = +1 / −1 / 0          (POSITIVE / NEGATIVE / NEUTRAL)
confidence   = min(1.0, 0.40 + 0.15·근거문장수)
```

주가 지도학습은 **측정해서 기각**했다 (`measure_news_salience.py`).

```
뉴스 특징 vs |잔차 수익률| Spearman ≈ 0   (n_sent +0.0067, n_comp +0.0062)
test rank-IC  기업평균만 +0.2056 → 뉴스 특징 추가 +0.2032 (하락)
```

`first_pos` 는 쓰지 않는다. 247,337건 중 246,514건이 0 이라 정보가 없다.

출력 → `company_document.{relevance_score, impact_score, confidence}`

이 값이 없으면 프론트(`lib/newsImpact.ts`)가 그 기업을 **통째로 건너뛴다**.
간접 전이도 여기서 출발하므로 같이 꺼진다.

### [8] 관계 추출 — 규칙 2종

| 입력 | 스크립트 | 산출 |
| --- | --- | --- |
| 한국어 기사 | `extract_relations.py` | SUPPLY / PARTNER / COMPETE |
| 영문 기사 | `extract_relations_en.py` | 같음. 표본 20건 정밀도 95% |
| DART 공시 | `build_grounded_disclosures.py` | INVEST |

영문판은 한국어 규칙을 못 쓴다. 조사(은/는, 에/에게)가 없어 어순과 동사로 방향을
정한다. 실측한 함정 다섯 가지는 `extract_relations_en.py` 헤더에 적어 뒀다.

SEC 공시는 쓰지 않는다. 8-K 항목 1.01 이 1,155건 있으나 표본 60건 중 51건이
사채·대출 등 **금융 계약**이었고, 지분보고서(13D/13G)는 수집 대상에서 빠져 있다.

### [9] 스냅샷 발행 — 가장 조심할 곳

```
새 run_id 생성 → 12개 버전 테이블 전부 채우기 → active_run 전환
```

`cosmos_analysis` 의 `current_*` 뷰는 **active_run 한 개만** 읽는다.
관계만 든 run 을 만들어 전환하면 뉴스 219,662건과 연결 246,117건이 통째로 사라진다.
**부분 run 을 만들면 안 된다.**

버전 테이블 12개: source_document, news_article, disclosure, company_document,
document_evidence, company_relationship, relationship_evidence,
relationship_score_current, relationship_score_history, company_metric_history,
graph_snapshot, graph_snapshot_load

---

## 적재 대상 테이블

| 테이블 | 무엇 |
| --- | --- |
| `source_document` / `news_article` | 기사 |
| `company_document` | 기사–기업 + 감성 + relevance/impact |
| `document_evidence` | 근거 문장 |
| `company_relationship` | 기업 간 관계 |
| `relationship_evidence` | 관계의 근거 문서 |
| `relationship_score_current` | 기간별 관계 점수 |
| `graph_snapshot` | 발행 단위 |

수집은 `public`, 서비스는 `cosmos_analysis` 다. **섞으면 버전 격리가 깨진다.**

---

## 운영에 필요한 것

| | 근거 |
| --- | --- |
| **GPU** | CPU 11문장/초 vs GPU 184문장/초 — **17배**. 밀린 182,926건은 CPU 로 며칠 |
| **스케줄러** | `deploy/stock-prices/*.timer` 의 systemd 패턴을 그대로 쓰면 된다 |
| **본문 정제 배치** | [2]. 없으면 [3]~[8] 전부 막힌다 |

## 쓰지 않는 것 — 파일은 있으나 연결하지 않는다

| 자산 | 이유 |
| --- | --- |
| `gat_seed{0,1,2}.pt` (GNN) | 주가 방향 정확도 39.51% < 시장 기준선 40.82%. 뉴스를 빼면 39.78% 로 **오른다** |
| `models/embedding/` | GNN 입력 전용 |
| `graphs.json`, `text_projection.npz`, `label_config.npz` | GNN 입력 전용 |
| `lightgbm_direction_*.txt` | 같은 주가 방향 목표 |

기존 U3 GNN 은 **관계 이웃의 상대 순위**용이지 뉴스로 주가를 맞히는 모델이 아니다.
둘을 같은 칸에 두고 설명하면 안 된다.

---

## 작업 순서

```
1) 본문 정제 배치        인프라   ← 선행조건
2) NER·링커 연결         AI       [3] 완성
3) B 분류기 연결         AI       [4]
4) 증분 분석 배치        AI       [5]~[8] 을 하나로
5) 스냅샷 발행기         AI       12개 테이블 전부
6) active_run 전환 절차  백엔드   누가 언제 어떻게 할지 합의
```

1번이 안 되면 나머지가 못 나간다.
