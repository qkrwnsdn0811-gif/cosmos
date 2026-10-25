# 기업 탭 주식 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기업 탭을 시세판 목록(`/companies`)과 기업 상세 페이지(`/companies/:id`)로 바꾸고, 이를 위한 시세 API 3개를 만든다.

**Architecture:** 백엔드는 JDBC 쿼리 저장소 + 서비스 + 컨트롤러(기존 `company`·`event` 패키지 방식)로 지수·전 종목 시세·관계 기업
시세를 낸다. 프론트는 순수 함수 모듈(`quoteFormat.ts`)에 계산·표시 규칙을 모으고, 화면 컴포넌트는 SVG로 직접 그린다.

**Tech Stack:** Spring Boot 3 / Java 21 / NamedParameterJdbcTemplate / JUnit5 + AssertJ, React 18 + TypeScript + react-query, node --test

**Spec:** `docs/superpowers/specs/2026-10-26-companies-stock-view-design.md`

## Global Constraints

- 범위: 주가가 있는 반도체 61종목(코스피·코스닥). 주가 없는 기업도 목록에 남기고 시세 칸만 null/'-'
- 등락은 거래일 수: 1일 = 1거래일 전 종가 대비, 1개월 = 20거래일, 3개월 = 60거래일. 미니 차트 = 최근 21개 종가(오래된 것부터)
- 지수 기간: 1M·3M·6M·1Y = 20·60·120·250거래일 + 기준점 1개
- 관계 기준: `graph_snapshot.status = 'PUBLISHED'` 스냅샷의 `relationship_score_current.window_type = '1Y'`
- 가격 등락 색: 오르면 `--rise`(빨강), 내리면 `--fall`(파랑). 이 두 화면에서만. `--pos`·`--neg` 는 건드리지 않는다
- 화면 문구는 관측값만. 오른다·내린다·사라·팔라·고평가·저평가를 말하지 않는다
- 차트 라이브러리를 새로 들이지 않는다(SVG 직접)
- 백엔드 테스트 환경(이 PC): `JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot" DB_URL=jdbc:postgresql://localhost:5432/cosmos_test REDIS_PORT=6380 JWT_SECRET=local-test-secret-local-test-secret-0123456789 ./gradlew test --tests '…'` (`cd BackEnd`). 아래 Run 줄은 이 앞부분을 줄여 `GT='…'` 로 적는다
- 커밋 메시지는 한국어 `feat:`/`docs:`, Claude 공동 작성자 줄을 넣지 않는다. 자기 파일만 경로로 커밋한다

## Review Focus

1. **거래일이 21개·61개보다 적은 종목**(최근 상장) — 1개월·3개월 등락은 null, 미니 차트는 있는 만큼. Task 2 테스트 `quotes_with_short_history`
2. **같은 상대와 관계가 여러 종류**(공급 + 함께 언급) — 상세 목록에 한 줄, 최고 점수 종류가 대표, 나머지는 otherTypes. Task 3 테스트 `related_merges_same_counterpart`
3. **주가가 있는 날과 지수가 있는 날이 어긋남**(휴장·데이터 누락) — 겹치는 날만으로 누적 수익률. Task 4 테스트 `alignByDate keeps only common dates`
4. **적자·재무 없음**(PER null, 순이익 < 0, 재무 행 없음) — PER '적자', 나머지 '-', 낮은 PER 칩에서 빠짐. Task 4 테스트 `perText and lowPer preset skip loss makers`
5. **없는 기업 id로 상세 진입** — API 404, 화면은 "기업을 찾을 수 없습니다"와 목록으로 가는 링크. Task 3 `related_unknown_company_404`, Task 6 브라우저 확인

---

### Task 1: 지수 API `GET /api/market/indices`

**Files:**
- Create: `BackEnd/src/main/java/com/cosmos/api/market/controller/MarketController.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/market/service/MarketService.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/market/repository/MarketQueryRepository.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/market/dto/{MarketIndexListResponse,MarketIndexResponse,IndexPointResponse}.java`
- Modify: `BackEnd/src/main/java/com/cosmos/api/global/security/SecurityConfig.java:31-39` (`/api/market/**` 를 PUBLIC_GET_PATHS 에)
- Test: `BackEnd/src/test/java/com/cosmos/api/market/service/MarketServiceIntegrationTest.java`, `SecurityConfigTest.java`(공개 경로 한 줄)
- Docs: `docs/api-specification.md` 에 절 추가

**Interfaces:**
- Produces: `record IndexPointResponse(LocalDate date, BigDecimal close)`,
  `record MarketIndexResponse(String indexCode, BigDecimal close, Double change1d, Double change1m, List<IndexPointResponse> points)`,
  `record MarketIndexListResponse(LocalDate asOf, List<MarketIndexResponse> items)` — items 는 KOSPI, KOSDAQ 순
- Produces: `MarketService.findIndices(String period): MarketIndexListResponse` — period 는 기존 `com.cosmos.api.company.type.StockPeriod`
  가 받는 1M·3M·6M·1Y 값, 그 외는 기존 방식대로 400(`CommonErrorCode.INVALID_INPUT` 계열)

- [ ] **Step 1: 실패하는 테스트** — `market_index_price` 에 KOSPI·KOSDAQ 각 70거래일 행(2025-01-02부터 평일, KOSPI 종가 = 1000 + i, KOSDAQ = 500 + i)을 넣고:
  - `indices_3m_has_61_points`: `findIndices("3M")` → items 코드 순서 `["KOSPI","KOSDAQ"]`, 각 points 61개, 마지막 점 = 마지막 거래일
  - `indices_changes`: KOSPI change1d = 1069/1068 − 1, change1m = 1069/1049 − 1 (1e-9 오차)
  - `indices_bad_period_400`: `findIndices("2W")` → BusinessException
  - (테스트 DB 의 다른 지수 행과 섞이지 않게, 테스트는 트랜잭션 안에서 기존 행을 지우고 넣는다)
- [ ] **Step 2: 실행해 실패 확인** — Run: `GT 'com.cosmos.api.market.*'` → 컴파일 실패(클래스 없음)
- [ ] **Step 3: 구현** — 저장소는 지수별 최근 `max(N+1, 21)` 개 종가를 날짜 내림차순으로 읽는다(N = 기간 거래일 수). 서비스가 등락과 points(오래된 것부터 N+1개)를 만든다. 컨트롤러 `GET /api/market/indices?period=3M`(기본 3M). SecurityConfig 공개 경로 추가
- [ ] **Step 4: 통과 확인** — Run: `GT 'com.cosmos.api.market.*' --tests 'com.cosmos.api.global.security.SecurityConfigTest'` → PASS
- [ ] **Step 5: API 문서** — `docs/api-specification.md` 엔드포인트 목록과 새 절(요청·응답 예시, 기간별 점 개수)
- [ ] **Step 6: 커밋** — `feat: 코스피·코스닥 지수 API (GET /api/market/indices)`

### Task 2: 전 종목 시세 API `GET /api/companies/quotes`

**Files:**
- Create: `BackEnd/src/main/java/com/cosmos/api/company/repository/CompanyQuoteQueryRepository.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/company/service/CompanyQuoteService.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/company/dto/{CompanyQuoteResponse,CompanyQuoteListResponse}.java`
- Modify: `BackEnd/src/main/java/com/cosmos/api/company/controller/CompanyController.java` (`GET /companies/quotes`, `/companies/{companyId}` 보다 앞에)
- Test: `BackEnd/src/test/java/com/cosmos/api/company/service/CompanyQuoteServiceIntegrationTest.java`
- Docs: `docs/api-specification.md`

**Interfaces:**
- Produces: `record CompanyQuoteResponse(UUID companyId, String name, String stockCode, String market, String industry, BigDecimal close,
  BigDecimal prevClose, LocalDate tradingDate, Double change1d, Double change1m, Double change3m, List<BigDecimal> spark,
  BigDecimal marketCap, Integer marketCapRank, String sizeClass, BigDecimal per, BigDecimal pbr, BigDecimal dividendYield,
  BigDecimal dividendPerShare, BigDecimal netIncomeTtm, BigDecimal equity, String fiscalBasis, LocalDate priceDate, int relationCount)`
- Produces: `record CompanyQuoteListResponse(LocalDate asOf, List<CompanyQuoteResponse> items)` — asOf = 전체 마지막 거래일
- Produces: `CompanyQuoteService.findQuotes(): CompanyQuoteListResponse` (상태 ACTIVE 전부, 시가총액 내림차순·없으면 이름순),
  `CompanyQuoteService.quotesById(): Map<UUID, CompanyQuoteResponse>` (Task 3 이 쓴다)

- [ ] **Step 1: 실패하는 테스트** — 테스트 기업 4개(id 접두 `7f000000-…`)를 넣는다: Q1(일봉 61개, 종가 100…160, 재무 PER 10·순이익 +), Q2(일봉 15개),
  Q3(일봉 없음, 재무만), Q4(일봉 61개, 순이익 −, PER null). 공개 스냅샷 1개와 1Y 관계 Q1–Q2(공급), Q1–Q4(함께 언급), 30D 관계 Q1–Q3:
  - `quotes_changes_and_spark`: Q1 close 160, prevClose 159, change1d = 160/159 − 1, change1m = 160/140 − 1, change3m = 160/100 − 1,
    spark 21개 = 140…160 (오래된 것부터)
  - `quotes_with_short_history`: Q2 change1d 있음, change1m·change3m null, spark 15개
  - `quotes_without_prices_or_fundamentals`: Q3 close·spark·change* null(spark 빈 목록), 재무 칸은 값 있음
  - `quotes_relation_count_uses_1y_only`: Q1 relationCount 2, Q3 0
  - 결과에서 테스트 기업만 골라 확인한다(테스트 DB 의 다른 기업과 섞이지 않게)
- [ ] **Step 2: 실행해 실패 확인** — Run: `GT 'com.cosmos.api.company.service.CompanyQuoteServiceIntegrationTest'` → 컴파일 실패
- [ ] **Step 3: 구현** — 저장소 쿼리 셋: ① 기업 + 대표 업종(`company_industry.is_primary`) + `company_fundamental` LEFT JOIN, ② `stock_price_history`
  (`interval_type = '1D'`)에서 `ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY trading_at DESC) <= 61` 종가, ③ 공개 스냅샷 1Y 관계의
  기업별 상대 수(`COUNT(DISTINCT 상대)`). 서비스가 셋을 합쳐 등락·spark 를 계산한다. 거래일 날짜는 `trading_at` 의 UTC 날짜
- [ ] **Step 4: 통과 확인** — 같은 Run → PASS. 이어서 `GT 'com.cosmos.api.company.*'` 로 기존 company 테스트도 PASS(경로 `/companies/{id}` 와 충돌 없음 확인: 컨트롤러 테스트에 `GET /api/companies/quotes` 200 한 줄)
- [ ] **Step 5: API 문서** — 새 절(필드 표, null 규칙)
- [ ] **Step 6: 커밋** — `feat: 전 종목 시세 API (GET /api/companies/quotes) — 등락·미니 차트·재무·1년 관계 수`

### Task 3: 관계 기업 시세 API `GET /api/companies/{id}/related`

**Files:**
- Modify: `CompanyQuoteQueryRepository.java` (관계 행 조회), `CompanyQuoteService.java` (`findRelated`), `CompanyController.java`
- Create: `BackEnd/src/main/java/com/cosmos/api/company/dto/{RelatedCompanyResponse,RelatedCompanyListResponse}.java`
- Test: `CompanyQuoteServiceIntegrationTest.java` 에 추가
- Docs: `docs/api-specification.md`

**Interfaces:**
- Consumes: `CompanyQuoteService.quotesById()` (Task 2)
- Produces: `record RelatedCompanyResponse(String relationType, String direction, Double score, List<String> otherTypes, CompanyQuoteResponse quote)` —
  relationType 은 `relationship_type.code`(SUPPLY·INVEST·PARTNER·COMPETE·CO_MENTIONED), direction 은 `OUT`(이 기업이 source, 방향 있는 관계)·
  `IN`(이 기업이 target, 방향 있는 관계)·`NONE`(방향 없는 관계)
- Produces: `record RelatedCompanyListResponse(UUID companyId, String window, List<RelatedCompanyResponse> items)` — window `"1Y"`, 점수 내림차순
- Produces: `CompanyQuoteService.findRelated(UUID companyId): RelatedCompanyListResponse`, 없는 기업은 `CompanyErrorCode.COMPANY_NOT_FOUND`(404)

- [ ] **Step 1: 실패하는 테스트** (Task 2 시드 재사용, 1Y 관계 추가: Q4→Q1 공급 점수 0.9, Q1–Q4 함께 언급 0.4, Q1→Q2 공급 0.7):
  - `related_directions`: `findRelated(Q1)` → Q4 행 relationType SUPPLY·direction IN(Q4가 Q1에 공급), Q2 행 SUPPLY·OUT
  - `related_merges_same_counterpart`: Q4 는 한 줄, score 0.9, otherTypes `["CO_MENTIONED"]`
  - `related_sorted_and_1y_only`: 순서 Q4, Q2. 30D 관계 Q1–Q3 은 없다
  - `related_unknown_company_404`: 없는 id → BusinessException(COMPANY_NOT_FOUND)
- [ ] **Step 2: 실행해 실패 확인** — Run: `GT 'com.cosmos.api.company.service.CompanyQuoteServiceIntegrationTest'` → 새 테스트 FAIL
- [ ] **Step 3: 구현** — 관계 행: 공개 스냅샷·1Y 의 `relationship_score_current` ⨝ `company_relationship` ⨝ `relationship_type`(code, directionality)에서 source 또는 target 이 이 기업인 행. 상대별로 최고 점수 행을 대표로, 나머지 종류는 otherTypes(중복 없이, 점수 순). 컨트롤러 `GET /companies/{companyId}/related`
- [ ] **Step 4: 통과 확인** — 같은 Run → PASS, `GT 'com.cosmos.api.company.*'` PASS
- [ ] **Step 5: API 문서** — 새 절(direction 뜻, 묶기 규칙)
- [ ] **Step 6: 커밋** — `feat: 관계 기업 시세 API (GET /api/companies/{id}/related) — 방향·같은 상대 묶기`

### Task 4: 프론트 데이터 층과 계산 규칙

**Files:**
- Modify: `FrontEnd/src/api/types.ts` (MarketIndex·CompanyQuote·RelatedCompany 타입), `FrontEnd/src/api/contract.ts`, `FrontEnd/src/api/live.ts`, `FrontEnd/src/lib/queries.ts`
- Create: `FrontEnd/src/features/companies/quoteFormat.ts`
- Test: `FrontEnd/tests/quoteFormat.test.ts`

**Interfaces:**
- Consumes: Task 1~3 응답 모양(필드 이름 그대로, 날짜는 `"YYYY-MM-DD"` 문자열)
- Produces (types.ts): `IndexPoint {date: string; close: number}`, `MarketIndex {indexCode: "KOSPI"|"KOSDAQ"; close: number; change1d: number|null; change1m: number|null; points: IndexPoint[]}`,
  `MarketIndices {asOf: string; items: MarketIndex[]}`, `CompanyQuote`(Task 2 필드, BigDecimal → number|null), `CompanyQuotes {asOf: string|null; items: CompanyQuote[]}`,
  `RelationDirection = "OUT"|"IN"|"NONE"`, `RelatedCompany {relationType: RelationshipType; direction: RelationDirection; score: number; otherTypes: RelationshipType[]; quote: CompanyQuote}`,
  `RelatedCompanies {companyId: string; window: string; items: RelatedCompany[]}`
- Produces (api): `api.market.indices(period: PricePeriod)`, `api.companies.quotes()`, `api.companies.related(id)`
- Produces (queries): `useMarketIndices(period: PricePeriod)`, `useCompanyQuotes()`, `useRelatedCompanies(id: string | null)` (staleTime 5분)
- Produces (quoteFormat.ts):
  - `fmtPrice(v: number|null): string` → `"1,263,000원"` / `"-"`
  - `fmtSigned(v: number|null): string` → `"+40,000"` / `"-1,000"` / `"0"`
  - `fmtPct(r: number|null, digits = 2): string` → 비율 0.0327 → `"+3.27%"`, `"-1.20%"`, `"0.00%"`, null → `"-"`
  - `fmtCap(krw: number|null): string` → 1조 이상 `"922.6조"`, 미만 `"932억"`, null `"-"`
  - `tone(r: number|null): "rise"|"fall"|"flat"`
  - `perText(q: Pick<CompanyQuote,"per"|"netIncomeTtm">): string` → `"5.6배"` / 순이익 < 0 이면 `"적자"` / `"-"`
  - `median(values: (number|null)[]): number|null` (null 제외, 짝수 개는 가운데 둘 평균)
  - `perShare(q): {eps: number|null; bps: number|null; roe: number|null}` — 주식수 = marketCap ÷ close
  - `relationLabel(type: RelationshipType, direction: RelationDirection): string` — 스펙 2절 표 그대로
  - `type PresetKey = "all"|"cap"|"up1m"|"down1m"|"lowPer"|"dividend"|"related"`; `PRESETS: {key: PresetKey; label: string}[]` (라벨: 전체·시가총액 상위·1개월 상승·1개월 하락·낮은 PER·배당주·관계 많은 기업)
  - `type SortKey = "cap"|"change1d"|"change1m"|"per"|"pbr"|"dividend"|"relations"|"name"`; `applyPreset(rows, preset): {rows: CompanyQuote[]; sort: SortKey; dir: "asc"|"desc"}`;
    `sortQuotes(rows, key: SortKey, dir): CompanyQuote[]` (null 은 방향과 상관없이 맨 뒤)
  - `sliceDays<T>(points: T[], period: PricePeriod): T[]` (마지막 N+1개, N = 20·60·120·250)
  - `alignByDate(stock: {date: string; close: number}[], index: {date: string; close: number}[]): {date: string; close: number; indexClose: number; stockReturn: number; indexReturn: number}[]` — 겹치는 날만, 첫 공통일 기준 누적 수익률
  - `rangePosition(low: number, high: number, v: number): number` (0~1로 자름, high = low 면 0.5)

- [ ] **Step 1: 실패하는 테스트** `tests/quoteFormat.test.ts`:
  - `fmtPct(0.0327) === "+3.27%"`, `fmtPct(-0.012) === "-1.20%"`, `fmtPct(0) === "0.00%"`, `fmtPct(null) === "-"`
  - `fmtCap(922.61e12) === "922.6조"`, `fmtCap(932e8) === "932억"`, `fmtPrice(1263000) === "1,263,000원"`, `fmtSigned(40000) === "+40,000"`
  - `perText and lowPer preset skip loss makers`: `perText({per: null, netIncomeTtm: -5}) === "적자"`, `perText({per: 5.61, netIncomeTtm: 1}) === "5.6배"`; `applyPreset(rows, "lowPer").rows` 에 적자·PER 없는 종목 없음, PER 오름차순
  - `median([3, null, 1, 2]) === 2`, `median([1, 2, 3, 4]) === 2.5`, `median([null]) === null`
  - `perShare({marketCap: 1000, close: 10, netIncomeTtm: 50, equity: 200})` → `{eps: 0.5, bps: 2, roe: 0.25}`
  - `relationLabel("SUPPLY","IN") === "공급사"`, `("SUPPLY","OUT") === "고객사"`, `("INVEST","IN") === "지분 보유사"`, `("INVEST","OUT") === "지분 투자처"`, `("COMPETE","NONE") === "경쟁사"`, `("CO_MENTIONED","NONE") === "함께 언급"`
  - `sortQuotes` 가 null 을 asc·desc 모두 맨 뒤에 둠
  - `alignByDate keeps only common dates`: 주가 날짜 [d1,d2,d3], 지수 [d1,d3,d4] → 결과 날짜 [d1,d3], d3 의 stockReturn = close3/close1 − 1
  - `sliceDays(80개, "3M").length === 61`, `rangePosition(10, 20, 15) === 0.5`, `rangePosition(10, 10, 10) === 0.5`
- [ ] **Step 2: 실행해 실패 확인** — Run: `cd FrontEnd && npm test` → quoteFormat 테스트 FAIL(모듈 없음)
- [ ] **Step 3: 구현** — quoteFormat.ts, 타입·api·쿼리 추가
- [ ] **Step 4: 통과 확인** — Run: `cd FrontEnd && npm test && npx tsc --noEmit -p .` → 전부 PASS, 타입 오류 없음
- [ ] **Step 5: 커밋** — `feat: 시세 API 프론트 연결과 표시·계산 규칙 (quoteFormat)`

### Task 5: 기업 목록 시세판

**Files:**
- Create: `FrontEnd/src/components/Sparkline.tsx`, `FrontEnd/src/features/companies/MarketStrip.tsx`, `FrontEnd/src/features/companies/QuoteTable.tsx`
- Modify: `FrontEnd/src/features/companies/CompaniesPage.tsx` (다시 쓰기), 그 화면의 CSS(지금 CompaniesPage 가 쓰는 파일), `FrontEnd/src/styles/global.css` (`--rise: #f04452; --rise-fg: #ff6b76; --fall: #3182f6; --fall-fg: #5c9dff;`)
- Delete (다른 곳에서 안 쓰면): `FrontEnd/src/lib/marketCapFallback.ts`

**Interfaces:**
- Consumes: Task 4 의 쿼리·`quoteFormat` 함수, 기존 `CompanyAvatar`, `useWatchedSet`/`useToggleWatch`, 기존 '투자·지분 관계' 보기
- Produces: `<Sparkline values={number[]} width? height? />` (마지막 ≥ 첫 값이면 `--rise`, 아니면 `--fall`, 값 2개 미만이면 빈 자리), `companyPath(id: string)` 를 `lib/routes.ts` 에 (`/companies/${id}`)

- [ ] **Step 1: 구현** — 스펙 1절 그대로: 시장 띠(지수 카드 2 + 등락 분포 막대 + 기준일), 골라보기 칩(`PRESETS`), 필터(검색·시장 KOSPI/KOSDAQ·산업·규모), 표(열 순서·오른쪽 정렬·`font-variant-numeric: tabular-nums`·머리 클릭 정렬·행 링크·별), 좁은 화면(≤ 760px)에서 기업·현재가·등락·1개월 추이만. 지수 API 실패 시 시장 띠만 숨김
- [ ] **Step 2: 확인** — Run: `cd FrontEnd && npm test && npx tsc --noEmit -p .` PASS. 브라우저 `/companies`(백엔드 18081 + 프론트 5173): 61종목 표, 기본 시가총액순 1위 삼성전자, 칩 '낮은 PER' 에 적자 없음, 열 머리 정렬 전환, 시장 필터 코스닥, 행 클릭 → `/companies/:id`, 콘솔 오류 없음, 375px 폭에서 가로 스크롤 없음. 스크린샷
- [ ] **Step 3: 커밋** — `feat: 기업 목록을 시세판으로 — 지수 띠·골라보기 칩·정렬되는 시세 표 (토스·Google Finance 참고)`

### Task 6: 기업 상세 페이지

**Files:**
- Create: `FrontEnd/src/features/companies/CompanyPage.tsx`, `ReturnChart.tsx`, `RangeBar.tsx`
- Modify: `FrontEnd/src/App.tsx` (라우트 `/companies/:companyId`, 기존 lazy 방식), `FrontEnd/src/features/invest/StockCard.tsx` (기업 이름 → `companyPath`), `FrontEnd/src/features/galaxy/CompanyPanel.tsx` ('기업 상세' 링크), 화면 CSS

**Interfaces:**
- Consumes: `useCompanyQuotes`(이 기업 시세·업종 비교·중앙값), `useStockPrices(id, "1Y")`(기존), `useMarketIndices("1Y")`, `useRelatedCompanies(id)`, `useCompanyHistory(id)`(기존), `quoteFormat` 함수, `Sparkline`, `companyPath`
- Produces: `<ReturnChart stock={…} index={…} mode="compare"|"price" />`, `<RangeBar low high value />`

- [ ] **Step 1: 구현** — 스펙 2절 그대로: 머리 줄(가격·등락 + 1일 범위·52주 범위 막대 + 시가총액 순위·거래대금 + 관심·관계 은하 버튼), 왼쪽(차트 기간 탭 1M·3M·6M·1Y 기본 3M + '지수와 비교' 켬/끔 + 마우스 세로선·말풍선 + 요약 한 줄 / 핵심 수치 격자 / 투자 지표 세 묶음과 업종 중앙값, EPS·BPS·ROE 에 "계산값" / 같은 업종 비교 표 + 중앙값 줄 + 한 줄 문장), 오른쪽(관계 기업 동향 요약 칩 + 목록 / 최근 뉴스 → `/?event=`), 좁은 화면 쌓기 순서. 빈 값·없는 기업·주가 없음 문구는 스펙 5절
- [ ] **Step 2: 확인** — Run: `cd FrontEnd && npm test && npx tsc --noEmit -p . && npm run build` PASS. 브라우저: SK하이닉스 상세에서 기간 탭·모드 전환, 마우스 말풍선 값이 일봉과 맞음, 1일·52주 범위 막대 위치, 업종 비교에 이 기업 강조·중앙값 줄, 관계 기업 클릭 → 그 기업 상세, 뉴스 클릭 → 이벤트 화면, 없는 id(`/companies/00000000-0000-0000-0000-000000000000`) → 안내 문구, 375px 폭, 콘솔 오류 없음. 이벤트 카드 기업 이름·은하 패널 링크로 상세 진입. 스크린샷
- [ ] **Step 3: 커밋** — `feat: 기업 상세 페이지 — 지수 비교 차트·범위 막대·투자 지표·업종 비교·관계 기업 동향 (토스·Google Finance 참고)`

### Task 7: 문서와 전체 확인

**Files:**
- Modify: `docs/nexus/existing-vs-new.md` (기업 목록 화면은 COSMOS 기존, 시세판·상세 페이지·시세 API 3개는 본선 신규, 커밋 열), `docs/superpowers/specs/2026-10-26-companies-stock-view-design.md` (구현하며 달라진 점이 있으면 끝에 짧게)

- [ ] **Step 1: 전체 테스트** — Run: `GT 'com.cosmos.api.market.*' --tests 'com.cosmos.api.company.*' --tests 'com.cosmos.api.event.*' --tests 'com.cosmos.api.global.security.*'` PASS, `cd FrontEnd && npm test && npm run build` PASS
- [ ] **Step 2: 문서 갱신**
- [ ] **Step 3: 커밋** — `docs: 구분표에 기업 탭 시세판·상세 페이지 반영`
