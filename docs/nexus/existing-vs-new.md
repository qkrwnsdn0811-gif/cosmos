# 기존·신규 구분표 (대회 규정)

대회 요강은 기존 구현과 본선 신규 개발 범위를 분명히 나누도록 요구한다. 이 문서는 git 기준점과 커밋을 근거로,
기능·데이터·모델 단위로 원래 COSMOS에 있던 것과 본선(2026-10-12 ~ 10-30)에서 새로 만든 것을 나눈다.

## 1. 기준점과 확인 방법

| 항목 | 값 |
|---|---|
| 기준점 | 태그 `pre-hackathon-baseline` → 커밋 `888321d` (2026-09-28 17:55, "COSMOS: 뉴스·공시 기반 기업 관계 그래프 서비스") |
| 원래 COSMOS | `main` 브랜치. 원격 `main`도 `888321d`로 기준점과 같다 |
| 본선 작업 | 로컬 `nexus` 브랜치, 기준점 뒤 커밋 99개 (2026-10-13 ~ 10-25). 원격 `NEXUS` 브랜치에는 10-19 커밋(`38fd0e9`)까지 올라가 있다 |
| 태그 위치 | `pre-hackathon-baseline` 태그는 아직 로컬에만 있다(원격에 없음). 원격에서는 `main` = `888321d`로 확인할 수 있다 |

확인 명령 (이 저장소에는 `nexus` 폴더도 있어서 끝에 `--`를 붙여야 브랜치로 읽는다):

```bash
git log --oneline pre-hackathon-baseline..nexus --
```

```bash
git diff --stat pre-hackathon-baseline nexus --
```

```bash
git diff --name-status pre-hackathon-baseline nexus --
```

결과 (2026-10-25, `a939c9d` 기준, 이 문서와 시연 시나리오 커밋 전):

- 파일 250개, +18,946줄 / −471줄. 새 파일 200개, 고친 기존 파일 50개, 지운 파일 0개.
- 1주 차(10-13 ~ 10-19, 커밋 91개): 파일 147개 +13,378 / −86. 투자자 화면(10-20 ~ 10-25, 커밋 8개): 파일 130개 +5,666 / −483.
- 그 뒤 커밋(모두 본선 신규): `1630019` 근거 문장을 뉴스 전 기사부터 보여 주기, `73e8ef4` 예상 변동폭(V13), `c7a2d84` '영향 종목'과 '이벤트 지도'를 첫 화면 하나로 합치기, `1804bcf` 첫 화면 값 연결 바로잡기, `2c5b840` 뉴스 유형 오분류 고치기.
- COSMOS의 Python 코드(`AI/`, `Crawling/`)와 배포 설정(`deploy/`, `Jenkinsfile`)은 한 줄도 바뀌지 않았다. 본선 코드는 COSMOS 모듈을 복사하지 않고 그 자리에서 불러 쓴다(`nexus/nexus/cosmos_reuse.py`).

## 2. 영역별 한눈에

| 영역 | 원래 COSMOS (기준점에 있음) | 본선에서 새로 만듦 | 본선에서 고친 기존 코드 |
|---|---|---|---|
| 수집·분석 (Python) | `AI/ner`(기업 사전 매처, NER), `AI/graph`(규칙 관계 추출, 관계 점수), `AI/gpu_news`(FinBERT), `Crawling/`(뉴스·공시·주가 수집기) | `nexus/` 79개 파일 +7,314줄 (테스트 27개 포함): 빅카인즈 클라이언트·수집, 기업 인식, 근거 관계, KPF-BERT 관계 판별기, 추정 전이, 초과수익률, 영향 모델, 뉴스 유형·반응표·딱지·적중률, 기업 지표, 적재 | 없음 |
| DB (Flyway) | V1 ~ V9: 기업·별칭·산업, 문서·뉴스·공시, 근거 문장, 관계·관계 점수, 그래프 스냅샷, 주가, 사용자·관심 기업·스크랩, 커뮤니티 | V10 `news_event`, `event_company_impact`, `event_abnormal_return`, `relation_evidence_snippet` / V12 `case_reaction_stat`, `event_company_signal`, `signal_backtest`, `market_index_price`, `company_fundamental` / V13 `company_expected_move`, `move_backtest` | V10: `company_relationship`에 관계 출처 열(`relation_source`) 추가 / V11: 출처에 함께 언급(`COMENTION`) 허용 |
| 백엔드 (Spring Boot) | `company`, `graph`, `news`, `user`, `community`, `global` 도메인 | `api/event` 22개 파일 +1,396줄 (테스트 +980): 이벤트 목록·지도·주가 차트·적중률·종목별 뉴스 이력 API / 기업 지표 조회(DTO·저장소) | `CompanyController`·`CompanyService`(지표 조회 +18줄), `GraphService`(은하 기본 시장을 코스피·코스닥으로), `SecurityConfig`(`/api/events/**` 공개), `TimeIdCursorCodec`(범위 밖 커서 400) |
| 프런트 (React) | 3D 기업 관계 은하(`three/`, `features/galaxy`), 기업·뉴스·커뮤니티·프로필·관심 목록 화면, 로고 아틀라스·로고 원판 셰이더, 기업 로고 267개 | `features/invest` 8개 파일 +1,473줄(투자자 첫 화면), `features/event` 6개 파일 +856줄(뉴스별 관계 지도, 10-25 첫 화면에 합치며 3D 지도만 남김), `store/invest.ts`, `lib/routes.ts`, `three/fit.ts`·`isolated.ts` 등, 로고 45개, 테스트 9개 | `App.tsx`·`Header`(첫 화면을 투자 피드로, 은하는 `/galaxy`), `api/*`·`lib/queries.ts`(새 API 연결), `three/` 13개 파일 +428 / −383(행성을 원형 로고 원판으로), 은하 화면(함께 언급 간선, 산업 버튼 숫자, 근거 문구) |
| 문서 | `README.md`, `docs/api-specification.md` 등 | `docs/nexus/`(실측·실행·검증 보고서), `docs/superpowers/specs`·`plans`(설계·계획) | `docs/api-specification.md`에 이벤트 API 절 +429줄 |

## 3. 기능 단위 (명세서 F1 ~ F14)

명세서(2026-10-20)의 '구분' 열은 그때까지의 NEXUS, 곧 본선 1주 차 결과까지 포함한 상태를 기준으로 적었다.
대회 기준점(2026-09-28 COSMOS)과 비교하면 명세서가 '기존'이라고 쓴 것 중 이벤트 근거 패널(F5), 빅카인즈 근거 문장(F6),
1·3거래일 초과수익률(F3)은 본선 1주 차(10-14 ~ 10-16)에 만든 것이라 신규다. 그래서 두 구분을 함께 적는다.

| ID | 기능 | 명세서 구분 | 기준점 대비 | 원래 COSMOS에 있던 것 | 본선에서 만든 것 | 커밋 |
|---|---|---|---|---|---|---|
| F1 | 원형 로고 행성 | 변경 | 변경 | 3D 은하, 로고 아틀라스, 로고 원판 셰이더, 기업 로고 267개 | 행성을 '구체 + 로고 칩'에서 원형 로고 원판 하나로, 이름은 호버·선택 때만. 토스증권 종목 아이콘을 첫 출처로 반도체 로고 45개 추가. 뉴스별 3D 태양계 지도 | `4b62768`, `98f57b6`, `6bcb351` |
| F2 | 뉴스 유형 분류 | 신규 | 신규 | 없음 | 제목·본문 앞 200자 키워드 규칙, 15개 유형 + 기타 (`nexus/nexus/cases/newstype.py`) | `46d9d23` |
| F3 | 과거 사례 반응표 | 신규 (초과수익률 계산은 기존 코드 확장) | 신규 | 주가 테이블 `stock_price_history`와 그 저장 규칙(거래일 UTC 자정) | 1·3거래일 초과수익률(10-14, `nexus/nexus/impact/abnormal.py`)에 5·20거래일 추가, 유형 × 기업 위치 × 기간 반응표(`case_reaction_stat`) | `9165bb9`, `46d9d23` |
| F4 | 방향 딱지 | 신규 | 신규 | 없음 | 명세 초안 규칙으로 이벤트 × 기업 × 기간 딱지(`event_company_signal`) | `46d9d23` |
| F5 | 영향 종목 카드 | 변경 (기존 이벤트 근거 패널 확장) | 신규 화면 (뉴스별 관련 기업 목록은 기존) | 뉴스 화면(`NewsPage`)에서 고른 기사가 직접 언급한 기업 목록과 관련도·영향 점수(감성 부호 +1/−1/0)·감성, 관계도(`RelationMap2D`) | 새 이벤트 API 위에 만든 카드: 기사 기업과 공급망 추정 기업, 딱지, 과거 사례, 영향 경로, 지표, 차트. 10-16 이벤트 근거 패널(`EventEvidencePanel`)을 10-25 종목 카드(`StockCard`)로 확장 | `ca39b5b`, `281205e` |
| F6 | 근거 문장·원문 링크 | 기존 재사용 | 일부 기존 | 은하의 관계 근거 패널(`RelationshipPanel`), 규칙 관계 분류기(`AI/graph/extract_relations.py`), 기업 사전 매처(`AI/ner/matcher.py`) | 빅카인즈 hilight 근거 조각(`relation_evidence_snippet`), KPF-BERT 관계 판별기, 카드·지도의 근거 표시 | `1cc9598`, `2a1c8d0`, `358ddae`, `ca39b5b` |
| F7 | 뉴스 전후 주가 차트 | 신규 | 신규 | 주가 테이블 | 시장 지수 테이블(`market_index_price`), 차트 API, `PriceChart` | `179029d`, `281205e` |
| F8 | 기업 지표 | 신규 | 신규 | 기업 화면(`CompanyPreviewDialog`), `company` 테이블, OpenDART 공시 수집기(`Crawling/disclosures/dart`, 재무 지표 계산은 없음) | KRX 상장 목록(시가총액·규모)과 OpenDART 재무제표(순이익·자본총계·배당)로 PER·PBR·배당수익률 계산(`nexus fundamentals`, 새 코드), `company_fundamental`, `/api/companies/{id}/fundamental` | `f462c68`, `179029d` |
| F9 | 투자자 유형 토글 | 신규 | 신규 | 없음 | 단기·장기 상태(`store/invest.ts`)와 피드·딱지·지표 전환 | `281205e` |
| F10 | 첫 화면 뉴스 피드 | 변경 | 변경 | 첫 화면 = 3D 은하 | `/` = 메뉴 '이벤트 지도': 뉴스 피드 + 고른 뉴스의 3D 관계 지도 + 영향 종목 카드. 은하는 `/galaxy`로 이동, 예전 `/events`는 첫 화면으로 넘김 | `281205e`, `c7a2d84` |
| F11 | 적중률 공개 | 신규 | 신규 | 없음 | 2026-07 이후 뉴스 검증(`signal_backtest`), `/api/events/backtest`. 10-25부터 화면에는 예상 변동폭 검증만 띄우고(`MoveCheckBanner`), 방향 적중률은 보고서에 둔다 | `46d9d23`, `179029d`, `281205e`, `1804bcf` |
| F12 | 투자 유의 문구 | 신규 | 신규 | 없음 | 모든 카드·화면 문구, 뉴스·주가·로고 출처 표기 | `281205e`, `fbfd2e6` |
| F13 | 종목별 뉴스 이력 | 신규 | 신규 | 기업 화면 | `/api/events/company-history`, 기업 화면의 `CompanyInvestSection` | `179029d`, `281205e` |
| F14 | 관심 종목 알림 | 신규 (기존 관심 목록 재사용) | 신규 (관심 목록은 기존) | 관심 기업 API `/api/users/me/watch-companies`, `user_watch_company` 테이블, 관심 목록 화면 | 관심 기업이 최근 뉴스 영향권에 들면 피드 위에 알림(`WatchAlerts`) | `281205e` |
| 추가 | 예상 변동폭 (명세서 밖, 10-25) | - | 신규 | 없음 | 종목이 뉴스 뒤 방향과 무관하게 얼마나 움직였는지(학습 기간 초과수익률 절댓값 평균, 3등분 등급)와 시험 기간 검증. 검증을 통과한 기간(단기)만 카드에 등급 표시, 첫 화면 '예상 변동폭 검증' 띠에 검증 결과 (`company_expected_move`, `move_backtest`) | `73e8ef4`, `c7a2d84`, `1804bcf` |

## 4. 명세서 목록 밖의 본선 작업 (1주 차, 10-13 ~ 10-19)

명세서는 아래를 "이미 만들어 두었다"고 적었지만, 모두 기준점 뒤 본선에서 만든 것이다.

| 작업 | 기존 COSMOS와의 관계 | 위치 | 대표 커밋 |
|---|---|---|---|
| 빅카인즈 API 수집 (`경제>반도체` 기사 355,446건, 2025-01 ~ 2026-10-13) | 신규. COSMOS 크롤러(네이버·야후 등)는 실행하지 않는다 | `nexus/nexus/bigkinds`, `ingest` | `9f0906c`, `8595f05`, `f721fc6` |
| 기업 마스터·기사별 기업 인식 (국내 상장 61개사) | COSMOS 기업 사전 매처와 기업·별칭 CSV(`AI/ner/data`)를 그대로 불러 쓴다 | `nexus/nexus/entities` | `e6eeb30`, `d3d9655` |
| 근거 관계 추출 (기업쌍 hilight, 관계 단어 검색, 지분 규칙) | COSMOS 규칙 관계 분류기(`AI/graph/extract_relations.py`)를 불러 쓰고, 지분 규칙과 검색 방식은 신규 | `nexus/nexus/relations` | `1cc9598`, `841b6b5`, `ae63294` |
| KPF-BERT 관계 판별기 | 신규. 2차 시험 정확도 81%로 적용, 처음 보는 문장만으로 다시 잰 3차는 75%, 서비스 관계 42개 사람 확인 39개 맞음 (`relation-model-report.md`) | `relations/kpfbert.py` | `2a1c8d0`, `358ddae`, `a34de02` |
| 기준 그래프 (은하에 발행된 관계: 근거 79 · 함께 언급 263 · COSMOS 90) | COSMOS 기존 관계(`AI/graph/data/db_full_20260922`)를 출처 `COSMOS`로 함께 쓴다. 화면에는 "COSMOS 기존 관계 데이터 (2026-09 기준)"로 표시 | `relations/graph.py` | `9fa6612`, `943db1f` |
| 2단계 추정 전이와 판단 보류 | 신규 코드 (핵심 기능). COSMOS에도 기사 기업에서 산업·1단계 관계로 노출 기업을 넓히는 코드(`AI/ner/impact.py`)가 있었지만 쓰지 않았다. 2단계 가중 전파, 판단 보류, 근거·추정 분리 저장은 본선에서 새로 만들었다 | `nexus/nexus/propagation` | `9e72e53`, `e7d88a0` |
| 초과수익률 | 계산 코드는 신규. 주가는 COSMOS 테이블에 COSMOS 규칙대로 저장 | `impact/abnormal.py`, `impact/prices.py` | `9165bb9`, `0aab494` |
| 기사 감성 (KR-FinBERT) | 모델은 COSMOS와 같은 고정 버전, 실행 코드는 신규 | `impact/sentiment.py` | `58bbc73`, `bf0c602` |
| 영향 모델 (다음 날 방향) | 신규. COSMOS의 영향 점수는 기사 감성의 부호였고 주가로 학습한 모델은 없었다. 시험 정확도 50.0%로 '항상 변동 없음'(49.9%)과 같아 딱지에 쓰지 않는다 (`impact-model-report.md`) | `impact/model.py` | `bb17555`, `494c1eb` |
| 이벤트 API | 신규 `/api/events/*` | `BackEnd/.../api/event` | `2ba5577`, `9d1d835`, `6b67492` |
| 뉴스별 관계 지도 (원형 2겹 → 3D 태양계 → 첫 화면에 합침) | 신규 화면. COSMOS 3D 장면 컴포넌트(`three/`)를 재사용. 10-25에 근거 패널·옛 영향 모델 표시를 지우고 첫 화면 뉴스 상세 안으로 옮겼다 | `FrontEnd/src/features/event` | `dd8bb30`, `ca39b5b`, `6bcb351`, `c7a2d84` |
| 빅카인즈 관계 은하 발행 | 빅카인즈 관계를 COSMOS 은하 테이블(`graph_snapshot`, `company_relationship`, `relationship_score_*`)에 발행. 은하 화면은 COSMOS 것 | `nexus/nexus/galaxy`, `load/galaxy.py` | `f6d78b8`, `5c7ef70`, `e7366f0` |

## 5. 데이터와 모델

| 구분 | 원래 COSMOS | 본선 |
|---|---|---|
| 뉴스 | 크롤러로 모은 뉴스(국내 뉴스·Yahoo Finance·네이버 API, `Crawling/news`) | 빅카인즈 API만 쓴다. COSMOS 크롤러는 실행하지 않았고, 로컬 DB의 문서 129,936건은 모두 출처 `bigkinds` |
| 기업 | 기업 사전·별칭 CSV (`AI/ner/data`) | 이 사전으로 만든 반도체 기업 마스터, 국내 상장 61개사로 제한 |
| 기업 관계 | `company_relationship` CSV(2026-09-22 덤프), 규칙 관계 분류 | 빅카인즈 근거 관계 79 · 함께 언급 263을 새로 만들고, COSMOS 관계 90개를 그대로 함께 쓴다 |
| 주가 | `stock_price_history` 테이블과 주가 수집기(`Crawling/prices`, 코스피·나스닥 각 100종목) | 같은 테이블에 FinanceDataReader 일봉을 새 코드로 채운다(코스닥 포함). 코스피·코스닥 지수 테이블은 신규 |
| 공시·재무 | OpenDART·SEC 공시 수집기(`Crawling/disclosures`) | OpenDART 재무제표 API(순이익·자본총계·배당)와 KRX 상장 목록으로 지표 계산 (`company_fundamental`, 새 코드) |
| 로고 | 기업 로고 267개 | 토스증권 종목 아이콘 45개 추가 (공식 공개 API가 아니라 시연용, 화면에 출처 표기) |
| 모델 | KR-FinBERT 감성(고정 버전), 감성 부호로 매긴 영향 점수, 규칙 관계 분류, 자체 NER | KPF-BERT 관계 판별기(신규 학습), KR-FinBERT 감성(같은 버전, 기사 단위 새 실행), 초과수익률로 학습한 영향 모델(신규, 미사용), 뉴스 유형 키워드 규칙(신규), 예상 변동폭 통계(신규) |

## 6. 이 표를 갱신할 때

- 10-25 이후 커밋이 생기면 1절의 명령을 다시 돌려 숫자와 3절의 커밋 열을 고친다.
- 태그를 원격에 올릴지는 따로 정한다. 올리지 않아도 원격 `main`이 기준점과 같다.
