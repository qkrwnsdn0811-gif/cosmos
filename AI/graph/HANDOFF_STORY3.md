# 스토리 3 (영향도) 인계 문서

**최신: 2026-09-15 v2 모델 학습·검증 완료, 서비스 연결 제외(사용자 결정).**
`README_NEWS_IMPACT_V2.md`를 먼저 참고한다. 모델 버전 `66c54ef0207b4783`.
실제 지수 라벨, 과거 DART 지분, 원문·NLI 검사 후 한국어 설명을 보강했다.
기사 29,827건 / 845,742쌍, 3시드 GAT. 26개 회귀 테스트와 실제 기사 12조건 재로딩을 검증했다.
방향 예측의 다수 기준선 대비 우위는 여전히 확인하지 못했다. 아래 §12는 v1 실측 기록이다.
Spring/프론트/운영 서버에 연결하지 않는다. 사용자 명시 요청 전 push/MR도 하지 않는다.

**추가 예측력 검증(2026-09-15):** `README_NEWS_IMPACT_EVIDENCE.md` 참고.
뉴스 내용 제거/기업 간 연결 제거 GNN을 각각 3시드 재학습해 8개 모델을 비교했다(624초).
기존 test 275,026쌍에서 NASDAQ 3일의 연결 제거·LightGBM 대비 방향 개선은 확인했지만,
전체 다수 클래스 대비 우위와 뉴스 내용 제거 대비 추가 이득은 확인하지 못했다.
30개 회귀 테스트·168개 통계 비교·1,600개 순위 교차 검증 통과. 원본 모델은 그대로 보존했다.
이는 이미 살펴본 test의 과거 대조 실험이며 새 독립 구간에서 입증한 결과가 아니다.

**2026-09-15 원래 Notion 명세 확인 후 범위 정정:** 뉴스 내용 → 기업별 방향·강도 예측과
GraphRAG 설명이 원래 스토리 3이다. 아래 U1/U3는 가격 충격/소스 기업만 입력한 별도 실험이며,
그 결과로 뉴스 조건부 예측을 불가능하다고 단정할 수 없다. 원래 명세의 세 작업은 구현·검증했고,
현재 안내는 `README_NEWS_IMPACT.md`, 실측 요약은 §12, 작업 이력은
`NEWS_IMPACT_WORKLOG.md`, 새 파이프라인은 `news_impact_data.py`, `train_news_impact.py`,
`serve_news_impact.py` 참고. §8-(A)와 원래 명세의 세 작업은 완료했다.
§3의 가격 누설 경고는 유지하되, "입력 스칼라 하나"는 기존 가격 실험의 제약이다.
새 모델에는 뉴스 텍스트·언급 정보를 넣으며, 관측 후 주가를 입력하지 않는다.

작성 2026-09-14 · 브랜치 `S15P21C205-48` · Jira https://ssafy.atlassian.net/browse/S15P21C205-48

마무리 갱신 2026-09-14: §8-(A) 완료. 양 시장 재실행 결과는 §2-3, 검증 기록은 §10.

**후속 사용자 결정: GNN을 주모델로 학습·서비스한다.** U3 모델과 추론 API를 구현했다.
현재 안내는 `README_IMPACT_GNN.md`, 후속 실측은 §11. 아래 계수표 채택 내용은 이전 단계의
결정 기록이며, 계수표는 이제 기준선으로 유지한다.

이 문서를 **먼저 끝까지 읽고** 작업을 시작할 것. 특히 "밟은 함정" 절은 건너뛰지 말 것 —
같은 함정을 다시 밟으면 그럴듯한 숫자가 나오는데 전부 틀린다.

---

## 0. 제품이 뭔가

SSAFY C205 "cosmos" — 뉴스에서 상장사를 뽑아 지식그래프로 잇고, 기사를 읽을 때
**"이 뉴스로 어느 기업이 영향받나"**를 보여주는 서비스.

유니버스는 **202종목** (KOSPI 100 + NASDAQ 102). AI 파트가 스토리 4개를 맡았다.

| 스토리 | 내용 | 상태 |
|---|---|---|
| 1 | 뉴스→기업 추출 (사전매칭 + NER) | 완료 (`AI/ner/`) |
| 2 | 지식그래프 엣지 + 관계 DB 적재 | 완료 (`AI/graph/`) |
| 3 | **영향도** | 이 문서 |
| 4 | 주가 검증 | 미착수 |

### 스토리 3 에 이미 주어진 것 (건드리지 말 것)

- `AI/graph/data/prices_daily.parquet` — 480,723행, 202종목, 2016-01-04~2026-09-11
  컬럼: `ticker, market, trading_at, open/high/low/close_price, adj_close, trading_volume`
- `AI/graph/data/edges_{sector,ownership,co_mention,correlation,relation_scored}.csv`
- `AI/graph/data/db/` — 관계 **317개** (PARTNER 236 / INVEST 44 / COMPETE 23 / SUPPLY 14)
- `AI/ner/data/company_industry.csv` — 202종목의 산업 18분류

---

## 1. 기존 U1/계수표 단계의 결론 (현재 GNN 채택은 §11)

1. **관계를 타고 다음 세션으로 전파되는 것은 0이다.** 이벤트 스터디로 확정.
2. **같은 세션 동조는 크고 확실하다.** 제품이 보여줄 것은 예측이 아니라 **귀속**이다.
3. **이번 비교에서 GNN 의 룩업테이블 대비 우위를 확인하지 못했다.** KOSPI 차이는
   유의하지 않고(t=-0.89), NASDAQ 은 표보다 낮다(t=-5.04). 같은 실행에서 측정 완료.

이 단계의 산출물은 **영향도 계수표**다. 현재 주모델은 §11 의 U3 GNN 이다.

---

## 2. 실측 숫자 (전부 재현 가능)

분할: **train 2017~2022 / val 2023 / test 2024-01-01~2026-09-11**.
모델 선택은 전부 val 로, test 는 마지막에 한 번만.

### 2-1. 전파 검정 (`event_study_propagation.py`)

이벤트 창 프로파일, excess bp (괄호 t). lag 0 = 이벤트 세션 자신.

```
group      lag-3     lag-2     lag-1      lag0      lag+1     lag+2     lag+3
ALL       0.9(0.3)  2.7(0.8)  9.8(2.9)  28.1(8.2)  8.0(2.6)  2.7(0.9)  4.6(1.5)
```

**lag+1 의 +8.0bp (t=2.6) 를 보고 "전파 있다"고 하면 안 된다.** 같은 크기가 이벤트
*이전*인 lag-1 에도 +9.8bp 로 있다. 짝지은 차분(사후 - 사전)을 하면:

```
ALL -1.50bp (t=-0.33)  |  악재만 +0.93 (0.12)  |  호재만 -3.51 (-0.66)
```

**0이다.** 팩터 제거 수준·이벤트 임계·변동성 전파까지 다 흔들어도 0이다.

### 2-2. 규칙 비교 (`build_impact_scores.py`)

평가 세 가지. **목표가 다르면 이기는 규칙이 다르다.**

| 평가 | 타깃 | 정답 | 뜻 |
|---|---|---|---|
| U1 | 그 시장 전 종목 | 잔차 | 학술적 비교용 (GNN 과 같은 잣대) |
| U2 | A 의 관계 이웃 | 잔차 | 화면이 보여주는 것 (부호 있음) |
| **U3** | A 의 관계 이웃 | **\|잔차\|** | **화면이 실제로 하는 것 (순위만)** |

KOSPI test rank-IC:

| 규칙 | U1 | U2 | **U3** |
|---|---|---|---|
| industry (같은 업종 1) | 0.0725 | 0.2034 | 0.1135 |
| industry_pair (18×18) | 0.0572 | 0.1487 | 0.0418 |
| pair_shrunk | 0.0762 | 0.2013 | 0.1017 |
| **pair_corr** | 0.0727 | **0.2154** | **0.2054** |
| relation_score (스토리2 단독) | 0.0397 | 0.1612 | — |

U3 에서 `pair_corr` 가 업종규칙 대비 **+0.1317 (t=+11.34)**. 수축(shrink)은 부호 없는
과제에서 **해롭다** — 업종쌍 평균으로 당기면 같은 업종 안에서 변별력이 죽는다.

NASDAQ 은 관계 이웃이 적어 주요 규칙의 U2/U3 이벤트가 340~515건뿐이다.
`pair_corr` 의 업종규칙 대비 t 는 U2 +0.21 / U3 +0.64 → **제품 순위에서 우위 확인 못함.**
마무리 때 CSV 를 대조해 "t 가 전부 2 미만"을 정정했다: U3 `pair_shrunk` 는 t=-2.72 로
오히려 나쁘다. 전체 횡단면 GNN 비교는 §2-3 과 별개로 읽을 것.

### 2-3. GNN vs 표 (2026-09-14 마무리 재실행)

같은 실행, 공통 test 이벤트 **KOSPI 9,486건 / NASDAQ 10,117건**.
GNN 은 시드 0/1/2 의 **점수 평균**으로 비교한다 (시드별 IC 평균과 다름).

| 모델 | KOSPI val | KOSPI test | NASDAQ val | NASDAQ test |
|---|---|---|---|---|
| 전엣지 동일가중 | 0.0175 | 0.0164 | 0.0481 | 0.0597 |
| relation 부호×점수 | 0.0440 | 0.0415 | 0.0278 | 0.0190 |
| co_mention npmi | 0.0681 | 0.0542 | 0.0442 | 0.0518 |
| 산업 동일가중 | 0.0927 | 0.0822 | 0.0604 | 0.0830 |
| **pair_shrunk (표)** | 0.1072 | **0.0838** | 0.0801 | **0.1089** |
| GNN 3홉 | 0.1081 | 0.0795 | 0.0720 | 0.0952 |

relation 은 공통 집합을 정할 때 제외한 좁은 모델이라 유효 이벤트가
KOSPI 6,663건 / NASDAQ 2,697건뿐이다. 이 행 평균을 다른 모델과 직접 빼지 말 것.

짝지은 차이 (GNN - 기준선, **세션 클러스터 t**):

| 기준선 | KOSPI 차이 (t), 627세션 | NASDAQ 차이 (t), 653세션 |
|---|---|---|
| 산업 동일가중 | -0.0027 (-0.38) | +0.0122 (+2.33) |
| pair_shrunk (표) | -0.0043 (-0.89) | -0.0138 (-5.04) |

KOSPI 는 val 에서 GNN 이 조금 앞서다 test 에서 뒤집혔지만 차이가 유의하지 않다.
이 사실만으로 과적합 원인까지 확정하지 않는다. NASDAQ 은 업종규칙보다 높아도 표보다
낮다. **두 시장 모두 이번 설정에서 표 대비 GNN 우위는 확인하지 못했다.**

인계 당시 KOSPI GNN val/test 는 0.1084/0.0804 였다. 이번 0.1081/0.0795 와 섞지 않는다.
시드만 고정했고 CUDA 결정론 설정은 꺼져 있어 수치의 완전한 일치를 보장하지 않는다.

### 2-4. impact_direction (`estimate_edge_direction.py`)

test 부호 적중률 (n=310):

| 규칙 | val | test |
|---|---|---|
| 유형 기본값 (COMPETE만 NEGATIVE) | 63.8% | 57.4% |
| **전부 POSITIVE (파라미터 0개 기준선)** | 68.8% | **63.5%** |
| 유형별 혼합 (\|r\|≥0.05) ← 채택 | 73.5% | **71.9%** |

유형별: COMPETE 95% (전부 POSITIVE 채택) / PARTNER 68% / SUPPLY 86% / INVEST 77%.
**COMPETE 에 NEGATIVE 를 주면 적중률 5%다.** 국내 동종업계는 같이 움직인다.

---

## 3. 밟은 함정 — 다시 밟지 말 것

### ① 산업 팩터를 뺀 잔차를 타깃으로 쓰면 산술이 새어든다 (test IC 0.47 이 나왔다)

`residualize()` 는 그룹의 leave-one-out 평균을 뺀다 → 그룹 안에서 잔차 합이 0에 묶인다.
산업 그룹 크기별 평균 잔차 상관을 재면 경제가 아니라 1/(n-1) 이 보인다:

```
그룹 크기      2~4개        5개         6개        9개       19~20개
평균 잔차 상관  +0.13~+0.56  -0.09~-0.16 -0.04~-0.14 -0.02~-0.05 -0.034~-0.044
```

MIN_GROUP=5 미만이라 팩터를 **안 뺀** 그룹만 양수다. 부호가 경제가 아니라 "뺐냐"로 갈린다.
→ **타깃 잔차는 `factors="market"` 만 쓸 것.**

### ② 횡단면 전체를 입력에 넣으면 어떤 팩터 정의를 써도 샌다

베타가 대체로 1이라 시장 전체에서도 잔차 합이 거의 0이다. "나머지 전부의 합" ≈ -(자기 자신).
→ **입력은 소스 한 종목의 스칼라 하나로 제한할 것.** 부수 효과로 다홉 전파가 안전해진다
(B 의 값이 입력에 없으므로 B→A→B 경로가 생겨도 샐 게 없다).

### ③ 기준선을 허수아비로 잡으면 이겼다고 착각한다

부호 작업을 처음에 "유형 기본값 55% → 66%" 로 보고했는데, 파라미터 0개짜리
"전부 POSITIVE" 가 59.7%였다. 그걸 안 놓고 비교한 것.
→ **항상 파라미터 0개 기준선을 같이 보고할 것.**

### ④ 추정한 공간과 쓰는 공간이 다르면 안 된다

부호를 `market+industry` 잔차로 추정했는데 모델은 `market` 잔차를 쓴다. ①의 기계적 음의
상관을 관계의 부호로 착각했다. 공간을 맞추니 이득이 사라졌다(63.5% → 63.5%).

### ⑤ 모델마다 채점한 이벤트 수가 다르면 비교가 아니다

커버리지가 좁은 모델이 쉬운 이벤트만 푼 셈이 된다. **공통 이벤트에서 비교할 것.**
단, 교집합을 정할 때 커버리지가 아주 좁은 모델(relation_score: KOSPI 63% / NASDAQ 23%)은
빼야 한다. 안 그러면 비교 자체가 거기로 쪼그라든다.

### ⑥ t 값은 세션 클러스터로 낼 것

같은 날 이벤트 수십 개는 독립이 아니다. 순진하게 세면 t 가 **1.5~1.6배** 부풀려진다.
`train_impact_gnn.paired_t()` 가 순진 t 와 클러스터 t 를 같이 준다.

### ⑦ 엣지에 미래정보가 들어간다

- `edges_correlation.csv` 는 2025-09~2026-09 가격으로 만들었다 = test 구간. **평가에서 제외.**
- `edges_co_mention.csv` 는 2012~2026 누적. 서버에서 `--until 2024-01-01` 로 다시 만든
  `data/edges_co_mention_pre2024.csv` 를 `--co-mention` 으로 넘길 것. (7,383 → 6,126쌍)
- `build_relationship_seed.py` 의 `corroboration()` 이 correlation 을 25% 섞는다.
  317관계 중 21개에서 실제로 max 를 차지한다. 실측 영향은 0.0001 로 무시할 수준이지만 알 것.

### ⑧ 시장 달력 정렬 (틀려도 겉으로 티가 안 난다)

KRX 06:30 UTC 마감, NASDAQ 20:00 UTC 마감. 같은 날짜 D 안에서 시간 순서는
**KOSPI(D) → NASDAQ(D) → KOSPI(D+1)**.
- 같은 세션(동시): 같은 시장이면 그 세션 자신, 교차시장이면 그 직전에 끝난 세션
- `baseline_onehop.source_index()` / `train_impact_gnn.aligned_tensors()` 가 이 규약.
- 쌍별 상관은 `build_correlation_edges.lag_align()` (NASDAQ t-1 → KOSPI t). 같은 날짜로
  맞추면 교차시장 상관이 -0.006 으로 죽는다.

### ⑨ 수익률은 반드시 `adj_close`

NASDAQ `close_price` 는 액면분할 미반영 (NASDAQ 행의 64.6%에서 불일치). 분할일이 -50%
폭락으로 잡힌다.

### ⑩ train 표준편차가 없을 때 `1`로 채우면 정답 크기가 왜곡된다 (U3 후속 감사)

train 표준편차가 없는 종목은 16개다. 기존 `sd=1` 대체는 이 기업의 test 잔차를 다른
기업보다 훨씬 작게 정규화한다. 상관 미추정값 0을 내는 표가 이를 맞히면 인위적인 이득이다.
다른 조건을 고정한 KOSPI 대조: 대체 시 pair_corr IC 0.2051(4,722건), 제외 시
0.0915(4,393건). 기존 §2-2 와 별개 감사 실행이며 숫자를 혼용하지 않는다.
**새 U3 모델은 train 표준편차가 없는 타깃을 평가에서 제외**하고, 서빙 때 이력 부족을
표시한다. 이전 실험 코드는 재현용으로 보존한다.

---

## 4. 파일

### 스토리 3 코드 (`AI/graph/`)

| 파일 | 역할 |
|---|---|
| `impact_dataset.py` | **공용 데이터 계층** — 잔차 패널, 시간분할, 엣지표, 계수행렬 |
| `event_study_propagation.py` | 전파 실재 여부 판정 |
| `baseline_onehop.py` | 파라미터 0개 베이스라인 (초기 탐색용) |
| `estimate_edge_direction.py` | 쌍별 impact_direction 추정 |
| `train_impact_gnn.py` | GNN + 전 모델 공통 이벤트 비교 |
| `build_impact_scores.py` | **최종 산출물 생성** |

각 파일 docstring 에 설계 근거와 실측 결과가 전부 적혀 있다. **코드 읽기 전에 docstring 부터.**

### 산출 데이터

| 파일 | 내용 |
|---|---|
| `data/impact_coefficients_relation.csv` | **568행 — 관계 엣지만. 제품용** |
| `data/impact_coefficients_top.csv` | \|계수\|≥0.15 — 확장 기능용 |
| `data/impact_coefficients.csv` | 35,532행 전체 조밀 행렬 — **진단용, 제품에 쓰지 말 것** |
| `data/impact_scores_eval.csv` | 규칙 비교 전체 결과 |
| `data/db/relationship_impact_direction.csv` | 317행 쌍별 부호 (DB 시드가 읽음) |

계수표 컬럼: `src_ticker, dst_ticker, src_market, dst_market, coefficient,
impact_direction, basis, src_industry, dst_industry, n_train_days, pair_corr, rule, fit_window`

`basis` 는 `relation+same_industry` / `same_industry` / `cross_industry` / `relation+cross_industry`.
분류별 신호 크기 (|계수| 중앙값): 관계+같은업종 0.131 > 같은업종 0.101 >> 관계+다른업종 0.035 >
다른업종 0.024. **신호는 같은 업종에 몰려 있고 나머지는 잡음이다.**

---

## 5. git 상태 (인계 당시 기록)

```
브랜치 S15P21C205-48 (dev 의 ee9d8c8 에서 분기)
커밋 2개, 모두 미푸시:
  ce263d2 test(S15P21C205-48): 관계 엣지의 다음날 전파를 이벤트 스터디로 검정해 ...
  860e23d fix(S15P21C205-48): 관계 영향 방향을 ... (※ 커밋 메시지의 55%→66% 는 틀린 숫자.
          이후 감사에서 63.5%→71.9% 로 정정됨. 코드·docstring 은 정정 완료)
```

**미커밋 (커밋해야 함):**
```
 M AI/graph/.gitignore
 M AI/graph/build_relationship_seed.py
 M AI/graph/data/db/relationship_impact_direction.csv
 M AI/graph/data/db/relationship_score_current.csv
 M AI/graph/estimate_edge_direction.py
 M AI/graph/impact_dataset.py
 M AI/graph/spark/build_comention_edges.py
?? AI/graph/build_impact_scores.py
?? AI/graph/train_impact_gnn.py
?? AI/graph/data/impact_coefficients*.csv       <- .gitignore 에 추가 필요
?? AI/graph/data/impact_scores_eval.csv         <- .gitignore 에 추가 필요
```

---

## 6. 환경

- **파이썬은 `AI/ner/.venv` 만 쓸 것.** 시스템 python 없음.
- 실행 시 `PYTHONUTF8=1` 필수 (콘솔 한글 깨짐)
  ```
  cd AI/graph && PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe <스크립트>.py
  ```
- torch 2.6.0+cu124, CUDA 사용 가능 (RTX 4050). GNN 학습 3시드 60에폭 ≈ 15분.
- **서버** (Spark/HDFS): `ubuntu@j15c205a.p.ssafy.io`, PEM `~/.ssh/J15C205T.pem`
  - `spark-submit` 은 `/opt/spark-4.2.0-bin-hadoop3/bin/spark-submit --master local[2]`
  - `hdfs` 는 비대화식 ssh 에서 PATH 에 없음 → `export PATH=$PATH:/opt/hadoop/bin`
  - **주의**: 공유 `spark-env.sh` 의 `SPARK_LOCAL_DIRS=/data/spark/local` 때문에 권한 오류가
    난 적 있음. 그때는 `SPARK_CONF_DIR=/home/ubuntu/ner-work/spark-conf` 로 우회했다
    (이미 만들어 둠). 공유 설정은 건드리지 말 것.
- **로컬 Postgres**: `BackEnd/docker-compose.yml` (cosmos/cosmos1234, DB cosmos, 5432)
  - Git Bash 에서 docker 경로가 깨지니 `MSYS_NO_PATHCONV=1` 필요
  - 적재: `docker cp AI/graph/data/db/. cosmos-postgres:/tmp/db` 후
    `docker exec -w /tmp/db -i cosmos-postgres psql -U cosmos -d cosmos -f load_relationships.sql`

### 재현 명령

```bash
cd AI/graph
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe event_study_propagation.py --profile -3 3
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe estimate_edge_direction.py
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_impact_scores.py
PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py \
    --seeds 3 --epochs 60 --hops 3 --co-mention data/edges_co_mention_pre2024.csv
```

---

## 7. 작업 규칙 (팀 관례)

- 브랜치는 **Jira 이슈 키만** — `S15P21C205-48`. 임의로 바꾸지 말 것.
- 커밋 메시지는 **한 문장**, 형식 `<type>(<이슈키>): <제목>`. **Co-Authored-By 금지.**
- **push 와 MR 은 사용자가 명시적으로 요청할 때만.** 알아서 푸시하지 말 것.
- 커밋 후 커밋 메시지를 사용자에게 알릴 것.
- **추정하지 말고 측정할 것.** 이 프로젝트에서 "아마 이럴 것이다"로 갔다가 틀린 적이 여러 번.

---

## 8. 다음에 할 일

### (A) 마무리 — 2026-09-14 완료

1. [x] **NASDAQ head-to-head 재실행.** 지정된 3시드·60에폭·3홉 명령이 양 시장 모두
   완료됐고 결과 CSV 32행을 저장했다. 공통 이벤트 결과는 §2-3.
2. [x] **docstring 의 옛 숫자 정리.** GNN 헤더와 부호 추정·계수표·공용 계층의 낡은 설명을
   정리했다. 과거 오류를 설명하는 "55% → 66%" 는 오류 이력으로만 남겼다.
3. [x] **`.gitignore` 에 `data/impact_coefficients*.csv`, `data/impact_scores_eval.csv` 추가**.
   §5 의 미커밋 작업과 이번 정리를 함께 로컬 커밋한다. push/MR 은 하지 않는다.

### (B) 서비스 연결 — AI API 완료, 웹 연결 미실행

현재 GNN API 계약은 `README_IMPACT_GNN.md` 참고. 아래는 이전 계수표 방식의 연결 검토
기록이다. 사용자는 이후 GNN을 선택했고, Spring/프론트 수정과 운영 배포는 아직 하지 않았다.

프론트 연결. `FrontEnd/src/features/news/NewsPage.tsx:400,432` 에 이미
2홉 전파 프로토타입이 있다:

```js
const DECAY = 0.82;                              // 근거 없는 상수
s = relevanceScore × impactScore × 100           // 직접 언급 기업
s = 앞단계점수 × (edge.score / 100) × DECAY       // 1~2홉
```

문제: `edge.score` 는 **관계 세기**(근거 기사 수·lift)지 전달계수가 아니다. 그리고
`impactDirection` 을 안 쓴다. 여기에 `impact_coefficients_relation.csv` 를 넣으면
DECAY 도 필요 없다(홉마다 계수를 곱하면 감쇠가 데이터에서 나온다).

선택지: ① 프론트에 상수로 심기(백엔드·DB 변경 0) ② DB 에 `impact_coefficient` 컬럼 추가
(V4 마이그레이션 + DTO) ③ 지금처럼 부호만 쓰기. **사용자가 아직 안 골랐다.**

> 참고: 제품이 "영향받을 기업 **순위만**" 보여준다면 서빙에 실시간 가격이 **필요 없다**.
> 한 기사에서 소스의 움직임은 모든 후보에 똑같이 곱해지는 스칼라라 순위를 못 바꾼다.
> 가격은 계수를 *추정*할 때만 쓴다. 이러면 "다음날 전파 0" 문제도 비켜간다.

### (C) 다음 후보 — "GNN 을 쓸 방법"을 찾는다면

사용자가 마지막에 물은 것. GNN 이 진 이유는 **입력이 스칼라 하나뿐이라 학습할 게
엣지 가중치 표뿐**이고, 그 표가 곧 `pair_corr` 이기 때문이다. GNN 이 구조적으로
유리한 자리를 찾아야 한다.

**① 이력 없는 쌍 (측정 준비 완료, 제일 빠름)**

관계 쌍 606개의 train 겹침 분포 — 이미 측정한 값:

| train 겹치는 세션 | 쌍 수 | 비중 |
|---|---|---|
| 60 미만 (쌍별 추정 **불가**) | 70 | 11.6% |
| 60~250 | 36 | 5.9% |
| 250~750 | 52 | 8.6% |
| 750 이상 | 448 | 73.9% |

종목 24개가 train 관측 250일 미만. 이력 **0일**인 것도 있다 —
삼성에피스홀딩스, LG씨엔에스, 산일전기, 에이피알. **정확히 뉴스가 많이 나는 종목들이다.**

가설: GNN 은 "이 회사는 반도체 업종이고 SK하이닉스와 PARTNER 이고 공동언급이 많다"는
구조에서 일반화하므로, 과거 가격이 없어도 예측이 나온다. 표는 원리적으로 불가능하다.

검정 방법: `train_impact_gnn.py` 의 공통 이벤트 비교에 **타깃 유니버스를 이력 부족
종목으로 제한한 평가를 추가**하고, 거기서 GNN vs `pair_corr` 를 비교한다.
이기면 산출물이 **하이브리드**가 된다 — 이력 충분하면 표, 부족하면 GNN.

**② 뉴스 내용을 조건으로 (잠재력 최대, 하루 이상)**

지금 계수는 쌍마다 **하나**다. 공급계약 뉴스는 SUPPLY 엣지로, 실적 뉴스는 동종업계로
다르게 전파돼야 하는데 정적인 표는 이걸 표현 못 한다. 스토리 1·2 가 재료를 이미 만들어
뒀다(멘션, 관계 유형, 근거 문장).

**③ 노드 늘리기 (제일 오래 걸림)**

202개는 GNN 에 작다. KOSDAQ 포함 수천 개로 늘리면 같은 파라미터에 데이터가 훨씬 많아진다.
가격은 pykrx 로 받을 수 있지만 멘션·관계도 다시 돌려야 한다.

---

## 9. 알려진 한계 (발표에서 먼저 말할 것)

- **NASDAQ 제품 순위의 우위는 확인 못함.** 관계 이웃이 적어 U2/U3 주요 규칙의 이벤트가
  340~515건뿐이다. `pair_corr` 의 업종 대비 t 는 +0.21/+0.64, U3 `pair_shrunk` 는 -2.72.
  스토리 2 의 관계 추출이 국내 기사 위주라서 생긴 일. 전체 횡단면 평가는 따로 읽을 것.
- **인과가 아니라 동조다.** 다음 세션 전파가 0인 이상 "A 가 오르면 B 가 오른다"가 아니라
  "같이 움직인다"로만 읽어야 한다. 수익 신호로 쓰면 안 된다.
- **INVEST 부호는 여전히 약점.** 유형 기본값(전부 POSITIVE) 84% > 쌍별 추정 77%.
  지분 관계는 prior 가 이미 좋아서 추정이 못 이긴다.
- **윈저라이즈가 전 구간 분위수를 쓴다** (`build_correlation_edges.log_returns`).
  train 구간 클리핑 경계가 test 구간 극단값에 영향받는 미세한 미래정보. 실측 영향은
  소수점 셋째 자리를 못 움직이지만 남아 있는 결함이다.
- **ownership/sector 엣지는 as-of 필터가 없다.** 지배구조·업종은 연 단위로 거의 안 변해서
  전 구간 유효로 뒀다.

---

## 10. 마무리 실행·검증 기록 (2026-09-14)

- 실행: `train_impact_gnn.py --seeds 3 --epochs 60 --hops 3 --co-mention data/edges_co_mention_pre2024.csv`.
  `AI/ner/.venv/Scripts/python.exe`, `PYTHONUTF8=1`, `PYTHONUNBUFFERED=1` 사용.
  약 13분, 정상 종료. 13,608엣지·52특징·market 잔차·직전 연도 베타.
- Python 3.12.14 / torch 2.6.0+cu124 / numpy 2.5.3 / pandas 2.3.3 / scipy 1.18.1,
  RTX 4050 Laptop GPU. 실행 중 모델 로직·설정·입력 데이터는 변경하지 않았다.
- 원본 결과 `data/impact_gnn_results.csv` (32행), 콘솔 로그
  `data/impact_gnn_story3_finish.log`, 입력 9개 파일의 SHA-256·버전·명령
  `data/impact_gnn_story3_inputs.log` 를 로컬에 보관했다. 재생성 가능한 파일들은 git 제외.
- 결과 CSV 에서 GNN/표/업종의 공통 test 이벤트 수가 시장별로 같은 것을 검증했다.
  일반 모델 행의 `test_t` 는 순진 t 이며, `[공통] GNN - ...` 행의 `test_t` 만 세션
  클러스터 t 다. 차이 행의 `n_events` 는 이벤트가 아니라 세션 수다.
- 부호 추정을 별도 출력 파일로 재실행: val 73.5% / test 71.9%, 유형별 혼합 |r|≥0.05.
  기존 부호표와 내용이 같고, DB 점수표의 317개 관계와 부호가 모두 일치했다.
- 휴장일을 포함한 교차시장 세션 정렬, 무효 이벤트 IC 의 원래 행 위치 유지,
  공통 이벤트의 희소 행 인덱스와 세션 마스크 검증 통과. 변경 대상 Python 7개 구문 검사 통과.
- 계수표 35,532행 / 관계 568행 / 확장 3,026행의 중복·자기쌍·부호·임계·규칙·추정창을
  검증했다. 규칙은 `pair_corr`, 내보내기 추정창은 `train+val`.
- 기존 평가 CSV 를 대조해 NASDAQ 의 "모든 t<2", 부호표의 옛 U2 수치,
  `pair_shrunk` 의 표본수 기반 수축 설명을 정정했다. 현재 수축은 고정 alpha=0.5 다.
- 학습 시작 시 train 관측이 없는 열의 `nanstd` 경고가 발생했다. 기존 코드의 표준편차
  대체 처리 후 실행이 완료됐다. §9 의 잔여 한계와 §8-(B)/(C) 후속 과제는 그대로 남는다.

---

## 11. GNN 주모델 채택과 U3 학습·서빙 (2026-09-14)

사용자가 GNN 채택을 명시했다. `train_impact_u3.py`는 단일 기업의 단위 입력과 그래프로
관계 이웃의 영향 **크기 순위**를 학습한다. 기존 `train_impact_gnn.py`는 U1 재현용으로 유지.
현재 서비스 모델은 `artifacts/impact_u3/model.pt`, CPU 추론은 `impact_ranker.py`,
API는 `serve_impact.py`다. 실행·연결 계약·한계는 `README_IMPACT_GNN.md` 참고.

| 시장 | GNN val IC | GNN test IC | pair_corr test IC | 짝지은 차이 | 세션 t | 공통 test 이벤트 |
|---|---|---|---|---|---|---|
| KOSPI | 0.1644 | 0.1349 | 0.1042 | +0.0306 | +2.69 | 4,351 |
| NASDAQ | -0.0238 | 0.1417 | 0.0312 | +0.1105 | +2.55 | 554 |

이번 설정에서 GNN이 표보다 높았다. NASDAQ은 val 59건뿐이고 val IC가 음수여서 안정성을
확정하지 않는다. U3의 후보·정규화 조건이 바뀌었으므로 §2-2/§2-3과 직접 비교하지 않는다.
학습 가중치·그래프·특징/종목 순서를 함께 저장했고 GPU 학습 출력과 CPU 재로딩 순위가
일치했다. API는 기업 코드를 받아 같은 대상 시장의 관계 후보만 반환한다.

추가로 해결한 것: 클리핑 경계를 2022년 말 이전 이력으로만 추정, train sigma 없는 타깃 제외,
가격에서 정한 관계 부호/점수와 집계 신뢰도 특징 제외. 기존 데이터 파일은 변경하지 않았다.

회귀 테스트 8개와 실제 Uvicorn HTTP 요청 검증을 통과했다. 로컬 30요청 응답시간 중앙값
15.9ms, 최대 25.6ms. 모델 버전 `38848187276d3333`, 번들 3,083,684바이트.

남은 범위: 뉴스 문장별 조건화, 최신 관계 스냅샷 반영 및 재평가, Spring/프론트 연결,
운영 배포. 현재는 같은 소스/시장이면 기사 내용이 달라도 같은 순위이며 가격 예측이 아니다.
pre2024 그래프는 val 기간 구조 정보를 포함하고 ownership/sector의 역사적 as-of는 없다.
**실행 가능한 AI 모델·내부 API를 완성한 상태이며, 웹 화면과 운영 서버에는 아직 연결하지 않았다.**

---

## 12. 원래 Notion 명세의 뉴스 입력 모델 (2026-09-15 완료)

사용자가 실제 Notion 명세를 보여 주었다. **뉴스 내용에 따른 방향·강도 예측은 원래 필수 기능**이며,
U3가 이를 대체한다고 설명한 것은 범위 해석 오류였다. 다음 세 작업을 새 파이프라인으로 완료했다.

1. 뉴스×기업 1/3거래일 라벨과 금융 감성 규칙·그래프 없는 LightGBM.
2. 동결 뉴스 임베딩·언급 특징을 직접 노드에 주입하는 PyG 2층 GATv2, 방향·강도 다중 과제,
   3시드 앙상블. 시드별 val 선택 epoch 5/2/1(0 기반).
3. 요청 시 그래프 근거 검색 + 실제 로컬 LLM의 근거 문장 선택·인용, 뉴스 입력 FastAPI.

산출물 `artifacts/news_impact/`, 모델 버전 `abc4be78e5933b99`. 코드와 계약은
`README_NEWS_IMPACT.md`, 서버는 `serve_news_impact.py`(localhost 8092).
기존 U3 8091 API는 별도 실험으로 보존. Spring/프론트 연결·운영 배포는 미실행.

HDFS에서 연도·국내외별 최대 1,500건을 결정적 표집해 32,621건 추출. 최종 **기사 29,835건 /
기사×기업 844,353쌍**, train 2017~2022 / val 2023 / test 2024~2026-09-11.
날짜만 있어 보수적인 가용 시점 **이후 첫 종가**부터 +1/+3세션을 잰 지연 반응 라벨이다.
즉시 반응이나 뉴스 하나의 인과 효과가 아니다. 공식 시장 지수 대신 동일 시장의 다른
유니버스 종목 동일가중 수익률을 제거했다. 라벨 종료가 분할 경계를 넘으면 제외한다.

| 시장·기간 | 다수 클래스 정확도 | 감성 규칙 | LightGBM | GAT |
|---|---|---|---|---|
| KOSPI 1일 | 43.07% | 28.81% | 41.92% | 41.91% |
| KOSPI 3일 | 43.35% | 28.99% | 41.89% | 42.43% |
| NASDAQ 1일 | 36.84% | 31.44% | 35.27% | 37.07% |
| NASDAQ 3일 | 35.74% | 31.32% | 32.19% | 35.81% |

공통 test 표본 KOSPI 152,253쌍 / NASDAQ 122,685쌍. **학습·서빙은 가능하지만, 방향 정확도가
충분하다고 볼 근거는 없다.** NASDAQ의 LightGBM 대비 개선을 다수 클래스 대비 개선으로
바꿔 말하지 말 것. 세션·HAC 비교, IC와 강도 지표는 README/평가 CSV 참고. test로 재선택하지 않았다.

추가 함정과 해결:

- 과거 기사 본문에 수집 시점 주가 위젯이 끼어 있었다. 첫 학습은 중단·격리하고
  `news_content.py`로 주가 위젯·삽입 뉴스·사이드바를 제거해 재학습. 전처리 적용 3,593건.
- 현재 sector/ownership를 과거에 소급하지 않음. 역사 구간에서는 미상 속성을 마스킹하므로
  이 두 유형의 역사적 효과까지 검증한 것은 아니다. 상관은 직전 연도 가격으로만 구성.
- 정확한 달력은 exchange_calendars: NASDAQ DST·조기 마감·과거 KRX 마감시간을 처리.
- 자유 생성 LLM이 2021 MOU와 2024 기사를 섞었다. 현재 GraphRAG는 **추출형**이며 생성 인용이
  원문에 실제 존재할 때만 반환. 인과 전망이나 별도 수치를 생성하지 않는다.
- 상수 예측 모델이 IC 평가에서 어려운 기사를 탈락시키지 않도록, 공통 기사 집합의 순위
  정보 점수(상수=0)를 통상 Spearman IC와 별도 기록. 비교 시 표본 수를 반드시 확인.

검증: 회귀 테스트 20개 통과, 서버/로컬 멘션 표본 201건 일치, CPU 재로딩 12조건 최대 오차 8.64e-7,
수익률·분할·그래프·확률/강도 범위 감사, 기사 내용 민감도와 위젯 변화 불변 확인.
실제 HTTP 일반 예측 warm 중앙값 141ms, 설명 생성 40.8초(단일 사례). 설명 전후 GNN 점수
동일. 테스트 서버는 종료. 재현 가능한 코드·가중치·전처리 설정·근거와 감사 로그를 보관했다.

앞으로 남은 것은 **방향 예측력 개선 연구**, 발행 시각/역사적 기업·관계 자료 보강,
그리고 Spring/프론트 연동·운영 배포다. 현재 모델을 정확도가 입증된 투자 예측 서비스로 소개하지 않는다.
