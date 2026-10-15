# NEXUS 계획 2: 이벤트 API와 원형 관계 지도 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 계획 1이 적재한 V10 테이블을 읽는 백엔드 `event` 도메인 API 2개와, 기사 1건의 근거(실선)·추정(점선) 관계를 원형 2겹으로 그리는 프론트 `/events` 화면을 만든다.

**Architecture:** 백엔드는 기존 `graph`·`news` 도메인과 같은 구조(Controller → Service → `NamedParameterJdbcTemplate` 쿼리 Repository → record DTO)로 `com.cosmos.api.event`를 추가한다. 프론트는 `features/event`에 목록 레일 + SVG 원형 지도 + 근거 패널을 둔다. 근거 조각은 지도 응답에 함께 실어 API를 2개로 끝낸다.

**Tech Stack:** Spring Boot 4.1.1, Java 21, PostgreSQL 17, JUnit 5 + Mockito + MockMvc(standalone), React 19, TypeScript 5.9, TanStack Query 5, Vite 8, Node 24 `node --test`

**Spec:** `docs/superpowers/specs/2026-10-13-nexus-bigkinds-design.md` (4.2 데이터 모델, 4.3 화면, 7 법적 고지). 입력 계약: `BackEnd/src/main/resources/db/migration/V10__add_nexus_event_tables.sql`, 계획 1 `docs/superpowers/plans/2026-10-14-nexus-plan1-semis-pipeline.md` Task 9

## Global Constraints

- 해커톤 신규 코드는 `com.cosmos.api.event`, `FrontEnd/src/features/event`, `FrontEnd/tests`에 둔다. 기존 파일은 연결에 필요한 곳만 고친다: `SecurityConfig.PUBLIC_GET_PATHS`, `SecurityConfigTest`, `App.tsx` 라우트, `Header.tsx` NAV, `api/types.ts`·`api/contract.ts`·`api/live.ts`, `lib/queries.ts`, `FrontEnd/package.json` scripts, `docs/api-specification.md`.
- V10은 Flyway로 이미 적용됐다. 스키마를 바꾸려면 V11을 만든다. V10을 고치지 않는다.
- 응답은 래퍼 없이 DTO를 그대로 반환하고, JSON은 camelCase, 시각은 `Instant`(UTC ISO 문자열), 날짜는 `LocalDate`, 점수는 `BigDecimal`이다.
- 오류는 `BusinessException(ErrorCode)`로 던진다. 입력 오류는 `CommonErrorCode.VALIDATION_FAILED`, 잘못된 커서는 `INVALID_CURSOR`.
- 커서는 `global/cursor/TimeIdCursor(dateline, eventId)`와 `TimeIdCursorCodec`을 재사용한다.
- 화면 색상은 `styles/global.css` 토큰(`--primary`, `--accent`, `--text-4`, `--pos`, `--neg` 등)만 쓴다.
- 이벤트 화면 하단에 고지 문구를 항상 표시한다: "관계·추정 결과와 초과수익률은 투자 권유가 아닙니다. 초과수익률은 이미 관측된 주가 변화이며 미래 주가 예측이 아닙니다."
- 1단계에는 영향 방향 모델이 없다. 노드 색은 근거/추정/판단 보류 구분에만 쓰고, 초과수익률은 숫자로만 보여준다 (`impactDirection`은 응답에 null로 둔다).
- 커밋 메시지에 Claude 표기를 넣지 않는다. 커밋은 작업 단위마다 로컬에만 한다.
- 이 Windows 환경의 Python 실행은 `-X utf8`이 필요하지만, 이 계획은 Gradle과 npm만 쓴다.

## Review Focus

1. **경로에 기업 마스터에 없는 key가 있는 경우:** 추정 경로 JSON의 `"MARKET:TICKER"`가 `company` 테이블에 없음 → 500이 아니라 해당 단계 이름을 null로 두고 응답해야 한다. (Task 2 테스트)
2. **추정 기업이 없는 이벤트:** 언급 기업만 있음 → 지도는 중심과 안쪽 원만 그리고, 상세 API도 정상 응답한다. (Task 2, Task 5 테스트)
3. **거래일이 없는 이벤트:** `event_trading_date`가 null(장 마감 후·최근 기사) → `tradingDate: null`, 초과수익률 빈 목록. 화면은 "-"로 표시한다. (Task 2 테스트)
4. **언급 기업이 많은 기사:** 언급 기업 8개 → 안쪽 원 노드가 서로 다른 각도에 놓여야 한다. (Task 5 테스트)
5. **변조된 커서:** 임의 문자열 커서 → 400 `INVALID_CURSOR`. (Task 3 테스트)

---

## 파일 구조

```text
BackEnd/src/main/java/com/cosmos/api/event/
  controller/EventController.java
  service/EventService.java
  service/EventPathParser.java          path JSONB → 단계 목록 (Jackson 3)
  repository/EventQueryRepository.java  SQL과 중첩 Row record
  repository/EventSearchCondition.java
  error/EventErrorCode.java
  dto/EventCompanyRefResponse.java, EventSummaryResponse.java, EventMapResponse.java,
      EventNodeResponse.java, PathStepResponse.java, EvidenceSnippetResponse.java, AbnormalReturnResponse.java
BackEnd/src/test/java/com/cosmos/api/event/
  service/EventServiceIntegrationTest.java
  controller/EventControllerTest.java
FrontEnd/src/features/event/
  EventsPage.tsx, EventRadialMap.tsx, EventEvidencePanel.tsx, radialLayout.ts, events.css
FrontEnd/tests/radialLayout.test.ts
```

---

### Task 1: 백엔드 실행 환경과 이벤트 목록 조회

**Files:**
- Create: `event/repository/EventQueryRepository.java`, `event/repository/EventSearchCondition.java`, `event/service/EventService.java`, `event/error/EventErrorCode.java`, `event/dto/EventCompanyRefResponse.java`, `event/dto/EventSummaryResponse.java`
- Test: `BackEnd/src/test/java/com/cosmos/api/event/service/EventServiceIntegrationTest.java`

**사전 준비 (이 작업의 첫 단계):**
- JDK 21이 없다. `winget install EclipseAdoptium.Temurin.21.JDK`로 설치한다. **PC에 소프트웨어를 설치하므로 실행 전에 사용자에게 확인받는다.** 설치 후 새 셸에서 `java -version`이 21을 출력해야 한다.
- `docker compose -f BackEnd/docker-compose.yml up -d` (postgres + redis).
- 기준선: 코드를 고치기 전에 `cd BackEnd && ./gradlew test`가 통과하는지 본다. 실패하면 기존 실패로 기록하고 이 계획의 테스트만 따로 돌린다 (`./gradlew test --tests 'com.cosmos.api.event.*'`).

**Interfaces:**
- Produces:
  - `EventErrorCode implements ErrorCode`: `EVENT_NOT_FOUND(HttpStatus.NOT_FOUND, "이벤트를 찾을 수 없습니다.")`. `code()`는 `name()`.
  - `record EventSearchCondition(String valueChain, UUID companyId, boolean hasInferred)`.
  - `record EventCompanyRefResponse(UUID companyId, String name, String stockCode, String market)`.
  - `record EventSummaryResponse(UUID eventId, UUID newsId, String title, String summary, String publisher, String originalUrl, Instant dateline, LocalDate tradingDate, String valueChain, List<String> categories, List<EventCompanyRefResponse> reportedCompanies, int inferredCount, int holdCount)`. `newsId`는 `source_document.document_id`(기존 `/api/news/{id}`와 같은 ID). `inferredCount`는 INFERRED·OK 수, `holdCount`는 INFERRED·HOLD 수. 리스트는 compact constructor에서 `List.copyOf`.
  - `EventQueryRepository`:
    - `List<EventRow> findEvents(EventSearchCondition condition, TimeIdCursor cursor, int limit)`: `status = 'ACTIVE'`만. `valueChain`이 null이 아니면 일치. `companyId`가 null이 아니면 그 기업의 `status = 'OK'` 영향 행(REPORTED 또는 INFERRED)이 있는 이벤트만. `hasInferred`가 true면 INFERRED·OK가 1개 이상인 이벤트만. 정렬 `dateline DESC, event_id DESC`, 커서 조건 `(e.dateline, e.event_id) < (CAST(:cursorTs AS TIMESTAMPTZ), CAST(:cursorId AS UUID))`, `LIMIT :limit`.
    - `Optional<EventRow> findEvent(UUID eventId)`.
    - `Map<UUID, List<EventCompanyRefResponse>> findReportedCompanies(List<UUID> eventIds)`: REPORTED 행을 `propagation_score DESC, name` 순으로. 빈 목록이면 쿼리하지 않는다.
    - `record EventRow(UUID eventId, UUID newsId, String title, String summary, String publisher, String originalUrl, Instant dateline, LocalDate tradingDate, String valueChain, List<String> categories, int inferredCount, int holdCount)`. `categories`는 `rs.getArray`, `tradingDate`는 `rs.getObject(..., LocalDate.class)`.
  - `EventService` (`@Service @Transactional(readOnly = true)`):
    - `CursorPageResponse<EventSummaryResponse> findEvents(String valueChain, UUID companyId, boolean hasInferred, String cursor, int size)`. `valueChain`은 null 또는 `SEMICONDUCTOR|BATTERY|AUTO`가 아니면 `VALIDATION_FAILED`. `size`는 1~100이 아니면 `VALIDATION_FAILED`. `LIMIT size+1`로 받아 `hasNext`를 정하고, 마지막 행으로 `nextCursor`를 만든다 (`NewsService`와 같은 흐름).

- [ ] **Step 1: 사전 준비** (위 목록. JDK 설치는 사용자 확인 후)

- [ ] **Step 2: 실패하는 테스트 작성** — `@SpringBootTest @Transactional`, `@Autowired EventService`, `JdbcTemplate`. `@BeforeEach`에서 고정 UUID(`7e000000-0000-0000-0000-0000000000NN`)로 다음을 넣는다: `data_source`(name `nexus-test-source`), `relationship_type` 4종(`ON CONFLICT (code) DO NOTHING`), 기업 4개(market `KOSDAQ`, stock_code `990001`~`990004`, status `ACTIVE`), `source_document`+`news_article`+`news_event` 3건 (value_chain `AUTO`, dateline 10:00/11:00/12:00), 영향 행:
  - 1번 이벤트: 언급 기업 1·2, 추정 OK 기업 3, 보류 기업 4
  - 2번 이벤트: 언급 기업 2, 추정 OK 기업 1
  - 3번 이벤트: 언급 기업 1뿐

  테스트:
  - `findsEventsNewestFirstForValueChain`: `findEvents("AUTO", null, false, null, 20)`의 eventId 순서가 3번, 2번, 1번이다.
  - `pagesWithCursor`: size 1이면 첫 페이지는 3번에 `hasNext=true`, `nextCursor`로 다음 페이지를 받으면 2번이다.
  - `filtersByCompanyAndInferred`: `companyId=기업 3`(1번 이벤트의 추정 OK 대상)이면 1번만. `hasInferred=true`면 3번이 빠진다.
  - `summaryCounts`: 1번 이벤트의 `reportedCompanies` 2개, `inferredCount=1`, `holdCount=1`.
  - `rejectsUnknownValueChainAndSize`: `"FOOD"`나 size 0이면 `BusinessException`이고 `getErrorCode() == CommonErrorCode.VALIDATION_FAILED`.

- [ ] **Step 3: 실패 확인**

Run: `cd BackEnd && ./gradlew test --tests 'com.cosmos.api.event.service.EventServiceIntegrationTest'`
Expected: 컴파일 실패 (`EventService` 없음)

- [ ] **Step 4: 위 Interfaces대로 구현**

- [ ] **Step 5: 통과 확인**

Run: 위와 같음
Expected: 5 tests passed

- [ ] **Step 6: 커밋**

```bash
git add BackEnd/src/main/java/com/cosmos/api/event BackEnd/src/test/java/com/cosmos/api/event
git commit -m "feat: 이벤트 목록 조회 서비스와 쿼리"
```

---

### Task 2: 이벤트 관계 지도 조회

**Files:**
- Create: `event/service/EventPathParser.java`, `event/dto/EventMapResponse.java`, `event/dto/EventNodeResponse.java`, `event/dto/PathStepResponse.java`, `event/dto/EvidenceSnippetResponse.java`, `event/dto/AbnormalReturnResponse.java`
- Modify: `event/repository/EventQueryRepository.java`, `event/service/EventService.java`
- Test: `EventServiceIntegrationTest.java` (테스트 추가)

**Interfaces:**
- Consumes: Task 1의 `EventRow`, `findEvent`, `EventSummaryResponse`, `EventErrorCode`
- Produces:
  - `record EvidenceSnippetResponse(String bigkindsNewsId, String title, String publisher, Instant publishedAt, String originalUrl, String snippet, BigDecimal confidence)`
  - `record PathStepResponse(UUID sourceCompanyId, String sourceName, UUID targetCompanyId, String targetName, String relationshipType, String edgeSource, BigDecimal strength, List<EvidenceSnippetResponse> evidence)`. 경로 key가 `company`에 없으면 id와 name은 null이다.
  - `record AbnormalReturnResponse(int windowDays, String benchmark, BigDecimal stockReturn, BigDecimal benchmarkReturn, BigDecimal abnormalReturn)`
  - `record EventNodeResponse(UUID companyId, String name, String stockCode, String market, String basis, int hop, BigDecimal score, String status, String holdReason, String impactDirection, BigDecimal confidence, UUID anchorCompanyId, List<PathStepResponse> path, List<AbnormalReturnResponse> abnormalReturns)`. `anchorCompanyId`는 INFERRED일 때 경로 첫 단계의 두 끝 중 이 이벤트의 REPORTED 기업이고, REPORTED면 null이다.
  - `record EventMapResponse(EventSummaryResponse event, List<EventNodeResponse> nodes)`. `nodes`는 REPORTED 먼저(점수 내림차순), 그다음 INFERRED(점수 내림차순).
  - `EventPathParser` (`@Component`, Jackson 3 `tools.jackson.databind.ObjectMapper` 주입): `List<RawStep> parse(String pathJson)`, `record RawStep(String src, String dst, String rel, String source, BigDecimal strength)`. 빈 문자열·`[]`는 빈 목록.
  - `EventQueryRepository` 추가 메서드:
    - `List<ImpactRow> findImpacts(UUID eventId)`: 기업 이름·코드·시장과 `path::text`를 함께.
    - `List<AbnormalReturnRow> findAbnormalReturns(UUID eventId)`
    - `Map<String, CompanyKeyRow> findCompaniesByKeys(Collection<String> keys)`: key는 `market || ':' || stock_code`.
    - `List<EvidenceSnippetResponse> findSnippets(UUID sourceId, UUID targetId, String relationshipType, int limit)`: `relation_evidence_snippet`에서 `published_at DESC`. 관계 종류가 `PARTNER`나 `COMPETE`면 양방향 모두 찾는다.
  - `EventService.findEventMap(UUID eventId) -> EventMapResponse`. 없으면 `EVENT_NOT_FOUND`. 경로 단계 중 `edgeSource == "NEWS"`인 것만 `findSnippets(..., 5)`로 근거를 채우고, 나머지는 빈 목록이다.

- [ ] **Step 1: 실패하는 테스트 추가** — Task 1 시드에 더한다: 1번 이벤트의 추정 OK 기업 3의 path `[{"src":"KOSDAQ:990001","dst":"KOSDAQ:990003","rel":"PARTNER","source":"NEWS","strength":0.85}]`, 보류 기업의 path에 마스터에 없는 key `"KOSDAQ:999999"`를 둔 단계. `relation_evidence_snippet` 1건을 **반대 방향**(기업 3 → 기업 1, PARTNER)으로 넣는다. 1번 이벤트 기업 1에 초과수익률 창 1·3. 2번 이벤트는 `event_trading_date` NULL. 테스트:
  - `mapOrdersReportedThenInferred`: `nodes`의 basis가 `[REPORTED, REPORTED, INFERRED, INFERRED]`이고 INFERRED는 점수 내림차순.
  - `inferredNodeHasAnchorAndNamedPath`: 기업 3 노드의 `anchorCompanyId == 기업 1`, `path[0].sourceName`·`targetName`이 시드 이름.
  - `newsStepCarriesSnippetFoundInEitherDirection`: 기업 3의 `path[0].evidence`가 1건이고 `snippet`이 시드 문장.
  - `unknownKeyInPathDoesNotFail`: 보류 노드의 해당 단계 `targetCompanyId == null`, `targetName == null`, `holdReason`이 시드 값.
  - `abnormalReturnsAttached`: 기업 1 노드의 `abnormalReturns` window 1, 3.
  - `eventWithoutTradingDateOrInferred`: 2번 이벤트 `event.tradingDate == null`. 3번 이벤트 노드는 REPORTED 1개뿐.
  - `unknownEventThrowsNotFound`: 임의 UUID면 `EventErrorCode.EVENT_NOT_FOUND`.

- [ ] **Step 2: 실패 확인**

Run: `cd BackEnd && ./gradlew test --tests 'com.cosmos.api.event.service.EventServiceIntegrationTest'`
Expected: 컴파일 실패 (`findEventMap` 없음)

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인**

Expected: 12 tests passed (Task 1의 5개 포함)

- [ ] **Step 5: 커밋**

```bash
git add BackEnd/src/main/java/com/cosmos/api/event BackEnd/src/test/java/com/cosmos/api/event
git commit -m "feat: 이벤트 관계 지도 조회 (경로·근거 조각·초과수익률)"
```

---

### Task 3: 이벤트 컨트롤러, 공개 경로, API 명세

**Files:**
- Create: `event/controller/EventController.java`
- Modify: `global/security/SecurityConfig.java:31-38` (`"/api/events/**"` 추가), `BackEnd/src/test/java/com/cosmos/api/global/security/SecurityConfigTest.java`, `docs/api-specification.md` (이벤트 절 추가)
- Test: `BackEnd/src/test/java/com/cosmos/api/event/controller/EventControllerTest.java`

**Interfaces:**
- Consumes: `EventService.findEvents`, `EventService.findEventMap`
- Produces (`@Validated @RestController @RequestMapping("/api/events")`):
  - `@GetMapping CursorPageResponse<EventSummaryResponse> findEvents(@RequestParam(required = false) String valueChain, @RequestParam(required = false) UUID companyId, @RequestParam(defaultValue = "false") boolean hasInferred, @RequestParam(required = false) String cursor, @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size)`
  - `@GetMapping("/{eventId}") EventMapResponse findEventMap(@PathVariable UUID eventId)`

- [ ] **Step 1: 실패하는 테스트 작성** — `EventControllerTest`는 `GraphControllerTest`와 같은 방식(서비스 mock + `MockMvcBuilders.standaloneSetup(...).setControllerAdvice(new GlobalExceptionHandler())`):
  - `listPassesFiltersAndReturnsCamelCase`: `GET /api/events?valueChain=SEMICONDUCTOR&hasInferred=true&size=5` → 200, 서비스가 `("SEMICONDUCTOR", null, true, null, 5)`로 호출, `$.items[0].eventId`, `$.items[0].dateline`이 ISO 문자열(`"2026-10-13T01:00:00Z"`), `$.hasNext`.
  - `listRejectsOutOfRangeSize`: `size=0` → 400, `$.code == "VALIDATION_FAILED"`.
  - `invalidCursorIs400`: 서비스가 `BusinessException(CommonErrorCode.INVALID_CURSOR)`를 던지면 400, `$.code == "INVALID_CURSOR"`.
  - `mapReturnsNodes`: `GET /api/events/{uuid}` → `$.nodes[0].basis == "REPORTED"`, `$.event.tradingDate == "2026-10-13"`.
  - `malformedEventIdIs400`: `GET /api/events/not-a-uuid` → 400.
  - `SecurityConfigTest`에 추가: 기존 `/api/news/probe`와 같은 방식으로 `/api/events/probe` probe를 두고, 비로그인 GET이 200이다.

- [ ] **Step 2: 실패 확인**

Run: `cd BackEnd && ./gradlew test --tests 'com.cosmos.api.event.*' --tests 'com.cosmos.api.global.security.SecurityConfigTest'`
Expected: 컴파일 실패 (`EventController` 없음)

- [ ] **Step 3: 구현** — 컨트롤러, `PUBLIC_GET_PATHS`에 `"/api/events/**"`. `docs/api-specification.md`에 기존 뉴스 절과 같은 형식으로 두 엔드포인트의 파라미터, 응답 필드, 오류 코드를 적는다.

- [ ] **Step 4: 전체 백엔드 테스트 통과 확인**

Run: `cd BackEnd && ./gradlew test`
Expected: BUILD SUCCESSFUL (Task 1 기준선에서 실패했던 기존 테스트가 있으면 그것만 그대로)

- [ ] **Step 5: 실제 데이터 확인** — `SERVER_PORT=18081 ./gradlew bootRun`을 백그라운드로 띄운 뒤 `curl "http://localhost:18081/api/events?valueChain=SEMICONDUCTOR&hasInferred=true&size=3"`이 3건을 돌려주고, 그중 하나의 `eventId`로 `curl http://localhost:18081/api/events/<id>`가 `nodes`를 돌려주는지 본다. 서버는 Task 6까지 켜 둔다.

- [ ] **Step 6: 커밋**

```bash
git add BackEnd/src docs/api-specification.md
git commit -m "feat: 이벤트 API 컨트롤러와 공개 경로, API 명세"
```

---

### Task 4: 프론트 API 연결과 이벤트 목록 화면

**Files:**
- Create: `FrontEnd/src/features/event/EventsPage.tsx`, `FrontEnd/src/features/event/events.css`
- Modify: `FrontEnd/src/api/types.ts`, `FrontEnd/src/api/contract.ts`, `FrontEnd/src/api/live.ts`, `FrontEnd/src/lib/queries.ts`, `FrontEnd/src/App.tsx` (lazy 라우트 `/events`), `FrontEnd/src/components/Header.tsx` (NAV에 `{ to: "/events", label: "이벤트" }`를 "뉴스" 다음에)

**Interfaces:**
- Consumes: Task 3 API (JSON 필드는 Task 1·2 DTO와 같은 이름)
- Produces:
  - `types.ts`: `ValueChain = "SEMICONDUCTOR" | "BATTERY" | "AUTO"`, `ImpactBasis = "REPORTED" | "INFERRED"`, `ImpactStatus = "OK" | "HOLD"`, 그리고 `EventCompanyRef`, `EventSummary`, `EvidenceSnippet`, `PathStep`, `AbnormalReturn`, `EventNode`, `EventMap` 인터페이스(Task 1·2 DTO와 필드 이름·순서 동일, `BigDecimal`은 `number | null`, `Instant`는 `ISODateTime`, `LocalDate`는 `string | null`), `EventListParams { valueChain?: ValueChain; companyId?: UUID; hasInferred?: boolean; cursor?: string; size?: number }`.
  - `contract.ts`: `events: { list(p: EventListParams): Promise<Page<EventSummary>>; detail(eventId: UUID): Promise<EventMap> }`.
  - `queries.ts`: `useEventFeed(params: Omit<EventListParams, "cursor">)` (`useNewsFeed`와 같은 무한 조회, queryKey `["events", "feed", params]`, size 20), `useEventMap(eventId: string | null)` (queryKey `["events", "map", eventId]`, `enabled: Boolean(eventId)`). 공개 API라 세션 키를 쓰지 않는다.
  - `EventsPage` (default export): `div.page.events-viewport` > `div.events-page`(그리드 `minmax(0,400px) minmax(0,1fr)`, 1100px 이하 한 열). 왼쪽 `section.card.events-rail`: "추정 있는 이벤트만" 토글(기본 켬, `hasInferred`), 이벤트 카드 목록(날짜 `fmtDateTime(dateline)`, 언론사, 제목 `clamp-2`, 언급 기업 칩 최대 3개, "추정 n · 보류 m"), "더 보기". 선택은 `?event=` 검색 파라미터와 동기화하고, 없으면 첫 이벤트를 선택한다. 오른쪽 `section.events-main`은 이 작업에서는 선택 이벤트 제목과 `Skeleton`만 둔다 (Task 5·6에서 채움). 하단에 Global Constraints의 고지 문구.

- [ ] **Step 1: 의존성 설치** — Run: `cd FrontEnd && npm ci`. Expected: 오류 없이 완료.

- [ ] **Step 2: 위 Interfaces대로 구현**

- [ ] **Step 3: 정적 검사**

Run: `cd FrontEnd && npm run typecheck && npm run lint && npm run build`
Expected: 세 명령 모두 오류 없음

- [ ] **Step 4: 브라우저 확인** — Task 3의 백엔드(18081)를 켠 상태에서 `.claude/launch.json`의 `cosmos-web`으로 개발 서버를 띄우고 `/events`를 연다. Expected: 헤더에 "이벤트" 메뉴, 왼쪽에 반도체 이벤트 카드 20건, "더 보기"로 20건 추가, 카드 클릭 시 주소의 `?event=`가 바뀜, 하단 고지 문구 표시. 스크린샷을 남긴다.

- [ ] **Step 5: 커밋**

```bash
git add FrontEnd/src
git commit -m "feat: 이벤트 API 연결과 이벤트 목록 화면"
```

---

### Task 5: 원형 배치 계산과 관계 지도

**Files:**
- Create: `FrontEnd/src/features/event/radialLayout.ts` (import 없는 순수 함수), `FrontEnd/src/features/event/EventRadialMap.tsx`, `FrontEnd/tests/radialLayout.test.ts`
- Modify: `FrontEnd/package.json` (scripts에 `"test": "node --test tests/"`), `FrontEnd/src/features/event/EventsPage.tsx`, `FrontEnd/src/features/event/events.css`

**Interfaces:**
- Produces (`radialLayout.ts`):
  - `INNER_RADIUS = 0.24`, `OUTER_RADIUS = 0.42` (지도 크기 대비 비율)
  - `interface LayoutInput { id: string; basis: "REPORTED" | "INFERRED"; anchorId: string | null }`, `interface Placed { id: string; x: number; y: number; ring: 1 | 2 }`
  - `radialLayout(nodes: LayoutInput[], size: number): Placed[]`. 중심은 `(size/2, size/2)`. REPORTED는 반지름 `INNER_RADIUS*size`에 -90°부터 같은 간격으로 둔다. INFERRED는 반지름 `OUTER_RADIUS*size`에, 앵커 기업 각도를 중심으로 폭 `360°/REPORTED 수`인 구간 안에 같은 간격으로 둔다. 앵커가 없거나 목록에 없으면 마지막 REPORTED의 구간에 넣고, REPORTED가 없으면 -90°부터 같은 간격으로 둔다. 입력 순서를 유지해 반환한다.
- Produces (`EventRadialMap.tsx`): `EventRadialMap({ map, selectedId, onSelect }: { map: EventMap; selectedId: string | null; onSelect: (companyId: string) => void })`. SVG `viewBox="0 0 520 520"`.
  - 중심: 원(반지름 34)과 "뉴스 이벤트", `fmtTradingDate(tradingDate)` 또는 "-".
  - REPORTED: 중심까지 실선, 채움 `var(--primary)`, 반지름 `10 + 8*score`.
  - INFERRED: 앵커까지(앵커가 없으면 중심까지) 점선 `stroke-dasharray="5 4"`. OK는 `var(--accent)`, HOLD는 `var(--text-4)` 불투명도 0.6. 반지름 `8 + 10*score`.
  - 노드 아래 이름, 그 아래 1일 초과수익률(`fmtPct(abnormalReturn*100, 1)`, 양수 `var(--pos)`, 음수 `var(--neg)`. 없으면 생략).
  - 범례: 실선 근거(기사 언급), 점선 추정(공급망 전이), 회색 판단 보류.
  - 노드는 `<g role="button" tabIndex={0} aria-label="{이름} {근거|추정|판단 보류}">`. 클릭과 Enter 키로 `onSelect`. 선택 노드에 테두리 강조.
  - `companyId`가 null인 노드는 그리지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/radialLayout.test.ts`, `import { test } from "node:test"`, `import assert from "node:assert/strict"`, `import { radialLayout, INNER_RADIUS, OUTER_RADIUS } from "../src/features/event/radialLayout.ts"`):
  - `single reported node sits at the top of the inner ring`: size 500, REPORTED 1개 → `x ≈ 250`, `y ≈ 250 - 0.24*500`.
  - `two reported nodes are opposite`: 각도 -90°, 90° (y 좌표가 중심 기준 위·아래).
  - `inferred node stays in its anchor's sector`: REPORTED A(-90°), B(90°), A에 앵커한 INFERRED 하나 → 중심 기준 y가 음수(위쪽).
  - `inferred nodes lie on the outer ring`: 중심까지 거리 ≈ `OUTER_RADIUS*size`.
  - `eight reported nodes get distinct angles`: REPORTED 8개 → 좌표 8쌍이 모두 다름.
  - `no inferred and empty input`: REPORTED만 → 모두 ring 1. 빈 입력 → `[]`.

- [ ] **Step 2: 실패 확인**

Run: `cd FrontEnd && npm test`
Expected: FAIL (모듈 없음)

- [ ] **Step 3: `radialLayout.ts` 구현 후 통과 확인**

Run: `cd FrontEnd && npm test`
Expected: 6 tests pass

- [ ] **Step 4: `EventRadialMap.tsx` 구현, `EventsPage` 오른쪽에 `.card.map-card` 안으로 배치** (`map-stage` 배경은 뉴스 화면과 같은 `#05070f`)

- [ ] **Step 5: 정적 검사와 브라우저 확인**

Run: `cd FrontEnd && npm run typecheck && npm run lint && npm run build`
Expected: 오류 없음. 브라우저에서 이벤트를 고르면 중심·안쪽 원(실선)·바깥 원(점선)·회색 보류 노드가 그려지고, 노드 클릭 시 강조가 옮겨 간다. 추정 기업이 없는 이벤트("추정 있는 이벤트만"을 끄고 고름)도 오류 없이 중심과 안쪽 원만 그린다. 스크린샷을 남긴다.

- [ ] **Step 6: 커밋**

```bash
git add FrontEnd/package.json FrontEnd/src/features/event FrontEnd/tests
git commit -m "feat: 원형 2겹 이벤트 관계 지도"
```

---

### Task 6: 근거 패널

**Files:**
- Create: `FrontEnd/src/features/event/EventEvidencePanel.tsx`
- Modify: `FrontEnd/src/features/event/EventsPage.tsx`, `FrontEnd/src/features/event/events.css`

**Interfaces:**
- Consumes: `EventMap`, `EventNode`, `PathStep` (Task 4)
- Produces: `EventEvidencePanel({ map, companyId }: { map: EventMap; companyId: string | null })`
  - 아무것도 선택하지 않음: 기사 제목, 언론사, `fmtDateTime(dateline)`, "원문 보기" 링크(`originalUrl`이 빈 문자열이면 숨김, `target="_blank" rel="noopener noreferrer"`), "언급 n · 추정 n · 보류 n".
  - REPORTED 선택: "기사에서 직접 언급(근거)", 초과수익률 표(1일·3일, `fmtPct(x*100, 2)`, 비교 지수 KOSPI/KOSDAQ, 없으면 "거래일 데이터 없음"), 원문 링크.
  - INFERRED 선택: 상태 배지("추정" 또는 "판단 보류 · {holdReason}"), 전이 점수 `fmtScore`, 경로 단계 목록 "{sourceName} → {targetName} · {relationshipMeta(관계).label}". 단계 아래:
    - `evidence`가 있으면 조각마다 snippet, 언론사, `fmtDate(publishedAt)`, 원문 링크.
    - 없으면 `edgeSource`에 따라 문구: `COSMOS` → "COSMOS 관계 데이터(2026-09 기준)", `COMENTION` → "같은 문장 동시언급 통계 (관계 근거 아님)", `NEWS` → "근거 조각 없음".
    - 이름이 null이면 "마스터에 없는 기업"이라고 표시.
  - 초과수익률 표는 REPORTED와 같다.

- [ ] **Step 1: 구현하고 `EventsPage`에서 지도 옆(넓은 화면 `minmax(0,1fr) 360px`, 좁은 화면 아래)에 배치. 지도에서 고른 노드를 패널에 넘긴다**

- [ ] **Step 2: 정적 검사**

Run: `cd FrontEnd && npm run typecheck && npm run lint && npm run build && npm test`
Expected: 모두 오류 없음

- [ ] **Step 3: 브라우저 확인** — Expected:
  - 기사 1건을 골랐을 때 언급 기업을 누르면 초과수익률 표가 보인다.
  - 추정 기업을 누르면 경로와 근거(뉴스 근거 조각 또는 COSMOS·동시언급 문구)가 보인다.
  - 판단 보류 기업은 사유가 보인다.
  - 원문 링크가 새 탭으로 열린다.
  - 375px 폭(모바일 프리셋)에서 한 열로 바뀌고 가로 스크롤이 생기지 않는다.
  - 데스크톱·모바일 스크린샷을 남긴다.

- [ ] **Step 4: 커밋**

```bash
git add FrontEnd/src/features/event
git commit -m "feat: 이벤트 근거 패널"
```

---

## 계획 범위 밖

- 영향 방향·크기 모델(노드 색·크기를 모델 값으로 바꾸는 일), 과거 유사 사례, 리스크 알림, 3D 은하 밸류체인 필터: 2단계 이후.
- 기존 뉴스 화면의 클라이언트 측 전파 계산(`NewsPage.tsx`의 `analyze()`)을 서버 데이터로 바꾸는 일: 이번에는 건드리지 않는다.
