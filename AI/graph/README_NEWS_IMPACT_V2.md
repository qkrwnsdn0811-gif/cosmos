# Story 3 · 명세 보강 버전

2026-09-15. 원본 Notion: [영향도 예측 모델 개발](https://app.notion.com/p/3cf75ef134b8805a8f9af72eb521df97).
사용자 결정: **서비스에는 연결하지 않고 모델 학습·검증까지만 완료한다.**
v1(`news_impact/`)은 그대로 보존하고 v2(`news_impact_v2/`)를 별도로 생성한다.
모델 선택은 2023 validation만 사용한다. 이미 살펴본 2024~2026 test를 반복 사용하므로
이번 결과는 기존 기간에서의 재평가이며, 새로 봉인한 독립 검증이라고 주장하지 않는다.

추가 예측력 대조 실험(2026-09-15): [README_NEWS_IMPACT_EVIDENCE.md](README_NEWS_IMPACT_EVIDENCE.md).
뉴스 내용/기업 간 연결을 제거한 GNN을 각각 3시드 재학습하고 종목별 통계까지 비교했다.
NASDAQ 3일에서 연결 제거·LightGBM 대비 방향 개선을 관찰했지만, 전체 다수 클래스 대비
우위와 뉴스 내용 제거 대비 추가 이득은 입증하지 못했다. 서비스 연결·원본 모델 교체는 하지 않았다.

## 이번 변경

- 시장 조정 라벨을 유니버스 종목 평균에서 **KOSPI / NASDAQ Composite 지수**로 변경.
  Yahoo Finance의 `^KS11`/`^IXIC` 종가이며 KRX에서 직접 받은 데이터라고 부르지 않는다.
  주식은 수정주가, 지수는 가격 지수다. 따라서 배당까지 완전히 일치한 총수익 지수 비교는 아니다.
- 과거 DART 사업보고서 **1,708건 / 82개 공시 기업**, 파싱 가능한 지분 표 **622건**, 유니버스
  내부 지분 기록 **297건** 확보. 기록 수는 고유 엣지 수가 아니다.
  보고서의 회계 기간과 접수일을 함께 사용해 당시 최신 보고서만 선택한다.
  과거 회계연도의 뒤늦은 정정이 더 최근 회계연도 지분 현황을 덮어쓰지 않는다.
  파싱 실패 보고서를 만나면 과거 지분을 계속 보유한다고 가정하지 않는다.
- 기사 **29,827건 / 기사×기업 845,742쌍**. 각 단계는 같은 후보·라벨·분할에서 비교한다.
- GraphRAG가 짧은 한국어 설명을 생성한다. 각 설명에 단일 출처와 정확한 원문 인용을 연결하고,
  숫자·날짜·계획/협약 표현을 검사한 후 별도 다국어 NLI 모델로 원문의 함의를 확인한다.
  NLI는 오류가 있는 필터다. 원문을 함께 반환하며 사실성 보증으로 소개하지 않는다.
- `POST /v1/impact/attention`: 3시드 평균 마지막 GAT 층의 실제 헤드별 어텐션.
  후보 기업으로 들어오는 모든 엣지와 자기 연결을 반환한다. 각 타깃/헤드별 합이 1이다.
  `path`는 별도의 그래프 탐색 경로이며 어텐션이 선택한 경로로 위장하지 않는다.
- 사전도 모델 산출물에 고정한다. 추론 번들 생성기는 HF 가중치를 번들 내부에 복사하고
  파일 해시를 남긴다. 다른 산출물 폴더를 참조하지 않는 번들을 만들 수 있다.

## 재현

저장소 루트, `PYTHONUTF8=1`, 기존 `AI/ner/.venv`를 사용한다.

```powershell
$env:PYTHONUTF8='1'
AI/ner/.venv/Scripts/python.exe AI/graph/news_impact_data.py --out AI/graph/artifacts/news_impact_v2 --index-dir AI/graph/artifacts/news_impact_v2 --ownership-history AI/graph/artifacts/news_impact_v2/historical_ownership.jsonl
AI/ner/.venv/Scripts/python.exe AI/graph/news_text_features.py --out AI/graph/artifacts/news_impact_v2
AI/ner/.venv/Scripts/python.exe AI/graph/train_news_impact.py --out AI/graph/artifacts/news_impact_v2 --seeds 3 --epochs 30
AI/ner/.venv/Scripts/python.exe AI/graph/audit_news_impact.py --out AI/graph/artifacts/news_impact_v2
AI/ner/.venv/Scripts/python.exe AI/graph/verify_news_service.py --out AI/graph/artifacts/news_impact_v2 --raw-news AI/graph/artifacts/news_impact/raw_news.jsonl --model-only
```

모델 전용 검증은 HTTP 서버를 시작하지 않는다. 추후 API 실행 시 CPU 설명은
`--explanation-device cpu`, GPU 설명은 `--explanation-device cuda`를 선택할 수 있다.
API 계약은 v1 README의 필수 입력/출력과 동일하다.
추가 어텐션 엔드포인트는 예측 요청과 같은 입력이며 `model_ver=stage3`만 허용한다.

`grounding.status`: `GENERATED`, `EXTRACTIVE_FALLBACK`, `GENERATION_REJECTED`.
`grounding.mode`: `verified_paraphrase` 또는 `extractive`.
숫자 모델 버전과 설명 버전(`explanation_version`)을 별도로 기록한다.
설명 생성은 숫자 모델 상태나 방향/강도를 수정할 수 없다.

지수 최초 수집: `collect_news_benchmarks.py --out <새 폴더>`.
해당 지수 자료에는 코스피 2017-09-22, 2017-12-20, 2022-01-03, 2022-05-09가 누락됐다.
KRX 대조는 로그인 자격정보가 없어 실패했다. 값을 추정하지 않았으며 달력은 유지하고,
누락일 및 다음 거래일 수익률이 걸친 라벨을 제외한다. NASDAQ 누락은 없다.

지분 최초 수집은 서버 개인 작업 폴더에서 `export_historical_ownership.py`를 실행한다.
입력은 기존 DART HDFS imports/snapshot, 출력은 개인 폴더의 JSONL이며 원본은 수정하지 않는다.
정확한 HDFS 경로는 각 출력의 `source_path`에 보존했다.
NLI 준비: `prepare_grounding_model.py --out <산출물 폴더>`.
모델은 [mDeBERTa-v3-base-mnli-xnli](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-mnli-xnli),
revision `8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c`. 한국어는 별도 학습 언어가 아닌 전이 언어라
한국어 사례 검증과 원문 노출을 유지한다.

## 웹 서비스 연결 계약과 실제 남은 의존성

이 브랜치의 Spring에는 뉴스 목록/상세 구현이 아직 없고, `source_document`에는 제목/요약과
HDFS 경로가 저장되며 원문 본문 컬럼은 없다. 프론트 `NewsDetail`에도 본문·지역이 없다.
요약을 원문처럼 보내거나 기존 프론트의 관계 점수 곱셈을 GNN 출력처럼 표시하지 않는다.

뉴스 서비스가 제공해야 하는 입력은 다음과 같다.

| 필드 | 연결 방법 |
|---|---|
| `news_id` | 서비스 뉴스 UUID를 그대로 전달 가능; 학습 원문 ID와 다를 수 있음 |
| `title`, `body` | 정제 전 원문 제목/본문을 HDFS 또는 뉴스 저장소에서 읽어 전달 |
| `published_at` | 실제 타임존 포함 시각; 날짜만 있으면 YYYY-MM-DD를 그대로 전달 |
| `region` | 수집 원본의 domestic/overseas; 종목 시장으로 추측하지 않음 |
| 출력 `ticker` | `company.ticker`와 조인해 서비스 UUID/기업명으로 변환 |

백엔드는 AI의 `impact_dir`/`impact_score`/`path`를 그대로 반환한다. 프론트는 상대 강도만
100배 표시할 수 있으며 `relevanceScore`, 기존 `edge.score`, `DECAY`를 다시 곱하지 않는다.
기업 클릭 때만 설명을 요청하고, 실패하면 근거 원문을 표시한다. `stage1` 사용 시 실제 단계가
사용자에게 드러나야 한다. AI 모델 장애를 프론트의 임의 점수로 숨기지 않는다.

서비스 연결은 사용자 요청으로 이번 범위에서 제외했다. Spring/프론트 수정, 실제 웹 연결,
운영 서버 배포를 하지 않았다. 작성 중이던 데모 화면도 제거했다.
push와 MR은 사용자 명시 요청 전까지 하지 않는다.

## 남아 있는 자료 한계

과거 업종 이력, 전체 기업의 지분 이력, 정확한 원문 발행 시각은 여전히 완전하지 않다.
현재 업종을 과거에 소급하거나 현재 시총을 학습 당시 시총으로 사용하지 않는다.
수집 날짜만 있는 기사는 가용 시점 이후 첫 종가를 기준으로 다음 1/3세션을 평가하므로,
즉각적인 장중 반응이나 뉴스 하나의 인과 효과를 측정하지 않는다.
성능 리포트와 기능 구현 완료를 구분하고, 관측된 성능보다 좋은 성과를 주장하지 않는다.

## 학습·검증 실측

모델 버전 **`66c54ef0207b4783`**, 3시드 선택 epoch **9/9/3**(0 기반).
LightGBM+GAT+평가 558.0초. 같은 기간을 이미 보았다는 제약을 유지한다.

| 시장·기간 | 다수 클래스 | 감성 규칙 | LightGBM | GAT |
|---|---:|---:|---:|---:|
| KOSPI 1일 | 45.55% | 27.61% | 43.20% | 43.16% |
| KOSPI 3일 | 46.83% | 27.60% | 42.93% | 42.04% |
| NASDAQ 1일 | 37.32% | 31.64% | 36.48% | 36.78% |
| NASDAQ 3일 | 35.86% | 31.17% | 33.82% | 36.71% |

공통 test 표본은 KOSPI **152,424쌍**, NASDAQ **122,602쌍**이다.
**GNN의 다수 클래스 대비 방향 예측 우위는 확인하지 못했다.** 세션을 동등 가중하고
HAC 5시차로 계산한 GNN−다수 기준선 t는 KOSPI 1/3일 **−6.81/−4.21**,
NASDAQ **−1.05/+1.45**다. NASDAQ 3일의 LightGBM 대비 t=+3.98을 다수 기준선 대비
우위로 바꾸어 표현하지 않는다. 쌍 가중 정확도 표와 세션 가중 차이를 직접 빼서 섞지 않는다.

GAT 강도 pooled IC는 KOSPI 1/3일 **0.1207/0.1376**, NASDAQ **0.2226/0.2080**.
이는 뉴스 내용만의 추가 효과나 인과성을 입증하는 지표가 아니다.
공통 기사 순위 점수는 각각 **0.0279/0.0814**, **−0.0107/0.0010**이다.

자료 감사는 기사 29,827건과 845,742쌍의 중복, 분할 경계, 과거 그래프 가용일,
확률·강도 범위, 모든 비교 행의 표본 수와 방향 정확도를 검사해 통과했다.
현재 업종 이력과 INVEST 원문 증거는 이 역사 그래프에서 비어 있다. 실제로 나타난 유형은
PARTNER/COMPETE/SUPPLY/ownership/co_mention/correlation이며, 비어 있는 유형의 학습 효과를
입증했다고 발표하지 않는다. 2023/2026 연초 ownership 방향 엣지는 각각 **32/74개**다.

회귀 테스트 26개 통과. `news_impact_v2_bundle/`의 모델·인코더·사전·검색 기사 번들은
46개 파일, **5,139,749,357바이트**이며 파일별 SHA-256을 보관한다.
서비스 연결과 운영 배포는 하지 않았다.

국내/해외 실제 기사 × 3단계 × 2기간의 CPU 재로딩 **12조건** 최대 오차는 **8.64e-7**.
기업별 입력 어텐션 헤드 합이 1인 것도 확인했다. 실제 기사에 대한 GraphRAG 설명은
**검증된 한국어 요약**으로 생성됐으며 GPU 설명 + CPU NLI 확인까지 **10.6초**(한 사례)였다.
설명 전후 방향·강도는 동일했다. 별도 가격 예측이나 HTTP 서버를 실행한 검증은 아니다.
근거 설명 예: “삼성전자와 한국전력공사는 전력 데이터 기반 '홈 에너지 솔루션' 개발을 위한
업무협약을 체결했다. [E2]” — 실제 2021-06-28 관계 원문을 인용했다.
단일 사례 시간을 전체 요청의 평균 지연으로 일반화하지 않는다.
