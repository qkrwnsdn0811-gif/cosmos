# NEXUS 계획 1: 반도체 슬라이스 데이터 파이프라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 빅카인즈 반도체 기사(2025-01 ~ 현재)를 수집해 기업 인식 → 근거 관계 → 규칙 기반 추정 전이 → 초과수익률까지 계산하고, 트랙 B가 읽을 PostgreSQL V10 테이블에 적재한다.

**Architecture:** 새 Python 패키지 `nexus/`가 전부를 담당한다. 빅카인즈 응답은 디스크에 캐시하고 정리본은 Parquet에 둔다. 분석은 메모리에서 하고 결과만 PostgreSQL에 적재한다. COSMOS의 `AI/ner/matcher.py`(기업 매처)와 `AI/graph/extract_relations.py`(관계 규칙)는 복사하지 않고 import해서 재사용한다.

**Tech Stack:** Python 3.12, requests, pandas, pyarrow, psycopg 3, FinanceDataReader 0.9.202, pytest, PostgreSQL 17 (`BackEnd/docker-compose.yml`)

**Spec:** `docs/superpowers/specs/2026-10-13-nexus-bigkinds-design.md`, 실측: `docs/nexus/bigkinds-api-findings.md`

## Global Constraints

- 뉴스는 빅카인즈 API로만 받는다. `provider_link_page`(원문 URL)에는 절대 접속하지 않는다.
- 기사 본문은 저장하지 않는다. DB에는 제목, 200자 요약, 원문 링크, 근거 구간(hilight 조각, 300자 이하)만 들어간다.
- 빅카인즈 응답 캐시와 Parquet은 `nexus/data/` 아래에 두고 git에 올리지 않는다. 테스트 픽스처는 실제 기사가 아닌 합성 데이터만 쓴다.
- access key는 저장소 루트 `.env`의 `BIGKINDS_ACCESS_KEY`에서 읽는다. 로그, 캐시 파일, 캐시 키에 key를 넣지 않는다.
- 빅카인즈 호출 간격은 최소 1.0초, 재시도는 3회(대기 2초, 5초, 15초)까지다.
- 기업 식별자는 `"MARKET:TICKER"` 문자열이다 (예: `KOSPI:005930`, `KOSDAQ:042700`, `NASDAQ:MU`).
- 근거(`REPORTED`)와 추정(`INFERRED`)은 저장 단계에서 분리한다. 추정 행에는 문서를 직접 연결하지 않는다.
- 이 Windows 환경에서 한글 출력이 깨지지 않도록 Python은 `-X utf8`로 실행한다.
- 해커톤 신규 코드는 `nexus/`와 `V10__*.sql`에만 둔다. COSMOS 기존 파일은 수정하지 않는다 (`.gitignore` 제외).
- 커밋은 작업 단위마다 로컬에만 한다. 형식: `<type>: <subject>` + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **빈 개체명 필드:** `tms_ne_organization`이나 `tms_ne_stream`이 빈 문자열이거나 없는 기사 → 오류 없이 제목·본문 매칭만으로 처리해야 한다. (Task 3, Task 4 테스트)
2. **장 마감 후·휴일 기사:** 15:30 이후 또는 주말·공휴일에 나온 기사 → 다음 거래일이 t0이어야 하고, 아직 오지 않은 거래일이 필요한 초과수익률은 행을 만들지 않아야 한다. (Task 8 테스트)
3. **한 기간 30,000건 초과:** 결과가 많은 기간 → 자동으로 반씩 나눠야 하고, 하루 단위로도 넘치면 조용히 잘리지 말고 오류를 내야 한다. (Task 2 테스트)
4. **같은 기간 재실행:** 수집·적재를 다시 돌리면 → 캐시에서 읽어 API를 다시 호출하지 않고, DB에는 중복 행이 생기지 않아야 한다. (Task 1, Task 9 테스트)
5. **동시언급만으로 이어진 추정:** 전이 경로가 `CO_MENTIONED` 간선만으로 이루어진 기업 → 점수와 무관하게 '판단 보류'여야 한다. 동시언급은 관계가 아니기 때문이다. (Task 7 테스트)

---

## 파일 구조

```text
nexus/
  pyproject.toml                  패키지 정의, 의존성, pytest 설정
  README.md                       설치·실행 방법 (짧게)
  nexus/
    __init__.py
    config.py                     Settings: 경로, .env 로딩
    models.py                     공용 dataclass (Article, Company, Edge, Impact 등)
    cosmos_reuse.py               AI/ner, AI/graph 모듈 import 경로 처리
    bigkinds/client.py            BigKindsClient: 캐시·간격·재시도
    bigkinds/search.py            기간 분할 수집, 상세 조회, 기업쌍 hilight
    ingest/normalize.py           응답 문서 → Article
    ingest/store.py               ArticleStore: Parquet 읽기·쓰기
    entities/master.py            시드 → 기업 마스터, 별칭 행 생성
    entities/recognize.py         Article → CompanyMention 목록
    entities/data/semis_seed.csv  반도체 밸류체인 시드 (사람이 검수)
    relations/comention.py        문장 단위 동시언급, NPMI
    relations/evidence.py         hilight → 근거 조각 + COSMOS 규칙 분류
    relations/graph.py            BaseGraph 구성
    propagation/propagate.py      규칙 기반 2단계 전파
    impact/prices.py              일봉 수집·캐시, 거래일 달력
    impact/abnormal.py            t0 결정, 초과수익률
    load/postgres.py              V10 테이블 적재
    pipeline.py                   CLI: collect / analyze / load
  tests/
    conftest.py                   합성 픽스처, PostgreSQL 임시 DB
    fixtures/bigkinds_docs.json   합성 빅카인즈 응답 (실제 기사 아님)
    test_*.py
BackEnd/src/main/resources/db/migration/V10__add_nexus_event_tables.sql
```

---

### Task 1: 패키지 뼈대와 빅카인즈 클라이언트

**Files:**
- Create: `nexus/pyproject.toml`, `nexus/README.md`, `nexus/nexus/__init__.py`, `nexus/nexus/config.py`, `nexus/nexus/bigkinds/__init__.py`, `nexus/nexus/bigkinds/client.py`
- Modify: `.gitignore` (추가: `nexus/data/`, `nexus/.venv/`, `.pytest_cache/`)
- Test: `nexus/tests/test_client.py`

**Interfaces:**
- Produces:
  - `Settings(repo_root: Path, data_dir: Path, bigkinds_key: str)`, `load_settings(repo_root: Path | None = None) -> Settings`. key는 환경변수 `BIGKINDS_ACCESS_KEY`가 우선이고, 없으면 `<repo_root>/.env`에서 읽는다. `data_dir` 기본값은 `<repo_root>/nexus/data`.
  - `class BigKindsError(RuntimeError)`: `result`, `reason` 속성
  - `class BigKindsClient(key: str, cache_dir: Path, transport: Callable[[str, bytes], tuple[int, bytes]] | None = None, min_interval: float = 1.0, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic)`
  - `BigKindsClient.post(path: str, argument: dict, *, cache: bool) -> dict`. 성공하면 `return_object`를 반환한다. `cache=True`면 `cache_dir/<path 슬래시를 _로>/<sha256(json.dumps(argument, sort_keys=True, ensure_ascii=False))>.json`에 응답 전체를 저장하고, 다음 호출에서는 transport를 부르지 않는다.
  - 기본 transport는 `requests.post("https://tools.kinds.or.kr" + path, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=120)`.

- [ ] **Step 1: 실패하는 테스트 작성** — 가짜 transport로 다음을 검증한다:
  - `test_post_returns_return_object`: transport가 `(200, b'{"result":0,"return_object":{"x":1}}')`를 주면 `post`는 `{"x": 1}`을 반환한다.
  - `test_body_contains_key_but_cache_file_does_not`: transport가 받은 body JSON의 `access_key`는 key이고, 캐시 파일 내용과 파일 이름 어디에도 key 문자열이 없다.
  - `test_cache_hit_skips_transport`: 같은 argument로 `cache=True`를 두 번 부르면 transport 호출은 1회다.
  - `test_cache_false_always_calls`: `cache=False`면 2회 호출한다.
  - `test_min_interval_sleeps`: 가짜 clock이 앞 호출 뒤 0.3초를 가리키면 `sleep(0.7)`이 호출된다.
  - `test_retry_on_5xx_then_success`: 504, 504, 200 순서면 성공하고 sleep 인자에 2와 5가 포함된다.
  - `test_gives_up_after_three_retries`: 504가 4번 오면 `BigKindsError`를 낸다.
  - `test_nonzero_result_raises_without_retry`: `{"result":1,"reason":"bad"}`면 재시도 없이 `BigKindsError(result=1)`를 낸다.
  - `test_load_settings_reads_dotenv`: `tmp_path/.env`에 `BIGKINDS_ACCESS_KEY=abc`가 있고 환경변수가 없으면 `load_settings(tmp_path).bigkinds_key == "abc"`다.

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `python -m venv nexus/.venv && nexus/.venv/Scripts/python -m pip install -e "nexus[dev]" && nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_client.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.bigkinds.client`)

- [ ] **Step 3: `pyproject.toml`, `config.py`, `client.py` 구현**

`pyproject.toml`: 패키지 이름 `nexus`, `requires-python = ">=3.12"`, 의존성 `requests`, `pandas`, `pyarrow`, `psycopg[binary]>=3.2`, `finance-datareader==0.9.202`, `pyahocorasick`, extra `dev = ["pytest>=8"]`, `[tool.pytest.ini_options] testpaths = ["tests"]`. README에는 설치 명령, 테스트 명령, "Crawling/news 크롤러는 해커톤 빌드에서 사용하지 않는다"는 문장을 적는다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_client.py -q`
Expected: 9 passed

- [ ] **Step 5: 커밋**

```bash
git add .gitignore nexus/pyproject.toml nexus/README.md nexus/nexus nexus/tests/test_client.py
git commit -m "feat: nexus 패키지 뼈대와 빅카인즈 클라이언트"
```

---

### Task 2: 기간 분할 수집, 상세 조회, 기업쌍 hilight

**Files:**
- Create: `nexus/nexus/bigkinds/search.py`
- Test: `nexus/tests/test_search.py`

**Interfaces:**
- Consumes: `BigKindsClient.post` (Task 1)
- Produces:
  - `DOC_FIELDS: list[str]`. 실측 문서 3절의 필드 전부 + `tms_ne_organization`, `tms_ne_person`, `tms_ne_location`, `tms_ne_stream`.
  - `search_documents(client, *, start: date, end: date, category: list[str] | None = None, query: str = "", today: date) -> Iterator[dict]`. `end`는 포함하지 않는다. 기간 `[start, end)`의 문서를 날짜 오름차순으로 낸다.
  - `fetch_details(client, news_ids: list[str], *, batch: int = 100) -> list[dict]`
  - `pair_hilights(client, a_name: str, b_name: str, *, start: date, end: date, size: int = 20, today: date) -> list[dict]`. 반환 문서에는 `news_id`, `title`, `hilight`, `dateline`, `provider`, `provider_link_page`가 있다.
- 캐시 규칙: 요청 기간의 `end <= today`이면 `cache=True`, 아니면 `cache=False`.

분할 알고리즘 (`search_documents`):

```text
probe = post(return_size=1)의 total_hits
if probe <= 30000: return_from = 0, 10000, 20000 순으로 return_size=10000 요청, 받은 수 합이 probe에 이르면 중단
elif 기간이 하루: raise BigKindsError(result=-1, reason="single day exceeds 30000")
else: 기간을 날짜 기준 절반으로 나눠 앞쪽, 뒤쪽 순서로 재귀
```

- [ ] **Step 1: 실패하는 테스트 작성** — `post`를 흉내 내는 가짜 클라이언트(요청 argument를 기록하고 기간별 `total_hits`를 사전으로 돌려줌)로:
  - `test_small_window_single_page`: 총 7건이면 probe 1회 + 페이지 1회이고, 7개 문서를 낸다.
  - `test_pages_until_total`: 총 25,000건이면 `return_from`이 0, 10000, 20000 세 번 호출된다.
  - `test_splits_large_window`: 2025-01-01~2025-03-01의 hits가 50,000이고 각 절반이 20,000이면 하위 기간 두 개로 나눠 수집한다.
  - `test_single_day_overflow_raises`: 하루짜리 기간의 hits가 40,000이면 `BigKindsError`를 낸다.
  - `test_cache_flag_by_today`: `end <= today`이면 `post`가 `cache=True`로, `end > today`이면 `cache=False`로 호출된다.
  - `test_fetch_details_batches`: 250개 ID는 100, 100, 50개씩 세 번 `news_ids`로 요청된다.
  - `test_pair_query_and_hilight`: `pair_hilights(…, "삼성전자", "SK하이닉스")`의 argument가 `query == "삼성전자 AND SK하이닉스"`, `hilight == 200`, `return_size == 20`이다.

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_search.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `search.py` 구현** — 요청 argument는 `{"query", "published_at": {"from", "until"}, "category", "sort": {"date": "asc"}, "return_from", "return_size", "fields"}`. `category`가 None이면 키를 넣지 않는다.

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_search.py -q`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/bigkinds/search.py nexus/tests/test_search.py
git commit -m "feat: 빅카인즈 기간 분할 수집과 기업쌍 hilight 조회"
```

---

### Task 3: 기사 정규화와 Parquet 저장소

**Files:**
- Create: `nexus/nexus/models.py`, `nexus/nexus/ingest/__init__.py`, `nexus/nexus/ingest/normalize.py`, `nexus/nexus/ingest/store.py`, `nexus/tests/conftest.py`, `nexus/tests/fixtures/bigkinds_docs.json`
- Test: `nexus/tests/test_normalize_store.py`

**Interfaces:**
- Produces (`models.py`):
  - `Entity = tuple[str, str]` (이름, 유형)
  - `@dataclass(frozen=True) Article(news_id: str, title: str, content: str, published_date: date, dateline: datetime, provider: str, byline: str, categories: tuple[str, ...], incidents: tuple[str, ...], link: str, orgs: tuple[str, ...], segments: tuple[tuple[Entity, ...], ...], nouns: tuple[str, ...])`. `dateline`은 KST(`+09:00`) aware datetime이다.
- Produces (`normalize.py`): `to_article(doc: dict) -> Article`
  - `tms_ne_stream` 파싱: `\n`으로 구간, `,`로 개체, 마지막 `:` 기준으로 이름과 유형을 나눈다. 빈 구간은 버린다.
  - `tms_ne_organization`, `tms_raw_stream`: `\n`으로 나누고, 공백 정리 후 빈 값은 버린다.
  - `dateline`이 없으면 `enveloped_at`, 그것도 없으면 `published_at`을 쓴다.
  - `provider_link_page`가 없으면 `link=""`.
- Produces (`store.py`): `class ArticleStore(root: Path)`
  - `write(articles: Iterable[Article], *, partition: str) -> int`: `root/partition=<partition>/part.parquet`에 쓴다 (덮어쓰기). 쓴 행 수를 반환한다.
  - `read(partitions: list[str] | None = None) -> list[Article]`: `news_id` 기준 중복 제거 (나중 파티션 우선).
- `conftest.py`의 픽스처 `synthetic_docs`는 `fixtures/bigkinds_docs.json`을 읽는다. 이 파일은 실측 응답 형식을 따르되 제목·본문·개체는 지어낸 값이다. 최소 4건: 기업 둘이 한 구간에 나오는 기사, 개체명 필드가 빈 기사, 15:40 출고 기사, 링크 없는 기사.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_parses_segments`: `"A사:OG,3%:QT\nB사:OG"` → `segments == ((("A사","OG"),("3%","QT")), (("B사","OG"),))`
  - `test_name_with_colon_uses_last_colon`: `"최태원#SK그룹 회장:PS_CLUE"` → `("최태원#SK그룹 회장", "PS_CLUE")`
  - `test_empty_entity_fields`: 개체명 필드가 없거나 빈 문서 → `orgs == ()`, `segments == ()`, 예외 없음
  - `test_dateline_kst`: `"2026-10-13T23:14:42.000+09:00"` → `dateline.hour == 23`, `utcoffset == 9시간`
  - `test_missing_link_is_empty_string`
  - `test_store_roundtrip_and_dedup`: 같은 `news_id`를 두 파티션에 쓰면 `read()`는 1건이고 나중 파티션의 제목을 갖는다.

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_normalize_store.py -q`
Expected: FAIL

- [ ] **Step 3: 구현** — Parquet은 pandas + pyarrow를 쓴다. 튜플 필드는 list 컬럼으로 저장하고 읽을 때 튜플로 되돌린다.

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_normalize_store.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/models.py nexus/nexus/ingest nexus/tests/conftest.py nexus/tests/fixtures nexus/tests/test_normalize_store.py
git commit -m "feat: 빅카인즈 기사 정규화와 Parquet 저장소"
```

---

### Task 4: 반도체 기업 마스터와 기업 인식

**Files:**
- Create: `nexus/nexus/cosmos_reuse.py`, `nexus/nexus/entities/__init__.py`, `nexus/nexus/entities/master.py`, `nexus/nexus/entities/recognize.py`, `nexus/nexus/entities/data/semis_seed.csv`
- Test: `nexus/tests/test_entities.py`

**Interfaces:**
- Consumes: `Article` (Task 3), COSMOS `matcher.CompanyMatcher`, `matcher.Alias` (`AI/ner/matcher.py`)
- Produces:
  - `cosmos_reuse.py`: `REPO_ROOT`, `import_matcher()` → `matcher` 모듈, `import_relations()` → `extract_relations` 모듈. `AI/ner`, `AI/graph`를 `sys.path`에 한 번만 넣는다.
  - `models.py`에 추가: `@dataclass(frozen=True) Company(key: str, market: str, ticker: str, name: str, value_chain: str, role: str)`, `@dataclass(frozen=True) CompanyMention(key: str, salience: float, sources: tuple[str, ...])`
  - `semis_seed.csv` 컬럼: `name,market_hint,role,aliases`. `role`은 `설계|제조|장비|소재|부품|후공정|고객` 중 하나다. `aliases`는 `|`로 구분한다 (예: `SK하이닉스` 행의 `SK하닉|하이닉스`). 국내 상장사 약 50개 + COSMOS `companies.csv`에 있는 나스닥 반도체 기업(엔비디아, 마이크론, AMD, 인텔, 퀄컴, 어플라이드머티어리얼즈, 램리서치, KLA, ASML 등)을 넣는다. 도메인 담당이 검수한다.
  - `build_master(seed_rows: list[dict], listing: pd.DataFrame, cosmos_companies: list[dict], value_chain: str) -> tuple[list[Company], list[str]]`. `listing`은 FinanceDataReader `StockListing("KRX")` 형식(`Code`, `Name`, `Market`)이다. 이름이 정확히 일치하는 행으로 ticker와 market(`KOSPI`/`KOSDAQ`)을 정한다. 나스닥은 `cosmos_companies`에서 찾는다. 찾지 못한 이름 목록을 두 번째 값으로 반환한다.
  - `alias_rows(companies: list[Company], seed_rows: list[dict], cosmos_alias_rows: list[dict]) -> list[dict]`. COSMOS `aliases.csv`와 같은 컬럼이다. `ticker` 칸에는 회사 key를 넣는다. 공식명은 `alias_type="official"`, 시드 별칭은 `"ko_manual"`, `ambiguity="safe"`. COSMOS 별칭은 해당 회사가 마스터에 있을 때만, `ambiguity`가 `safe`인 행만 가져온다.
  - `class Recognizer(companies: list[Company], aliases: list[dict])`, `Recognizer.recognize(article: Article) -> list[CompanyMention]`, `Recognizer.key_of_org(name: str) -> str | None`, `Recognizer.aliases_of(key: str) -> list[str]` (그 회사의 safe 별칭 전부, 긴 것부터)
    - 제목에 매칭: salience 1.0, source `"title"`. COSMOS `CompanyMatcher.match(title, None)`을 쓰고 confidence 0.5 이상만 인정한다.
    - 본문 200자에 매칭: 0.8, `"content"`. `match(None, content)`
    - `orgs`에만 있음: 0.6, `"orgs"`. 별칭 사전에서 정확히 일치하는 이름만 인정한다.
    - 같은 회사가 여러 곳에 나오면 salience는 최댓값, sources는 합집합이다. 결과는 salience 내림차순, key 오름차순이다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_build_master_resolves_krx_and_nasdaq`: 가짜 listing(`Code="042700"`, `Name="한미반도체"`, `Market="KOSPI"`)과 COSMOS 행(`ticker="MU"`, `market="NASDAQ"`, `name_official="마이크론"`)이면 key `KOSPI:042700`, `NASDAQ:MU`가 나온다.
  - `test_build_master_reports_unresolved`: listing에 없는 이름은 두 번째 반환값에 들어간다.
  - `test_kosdaq_market_from_listing`: `Market="KOSDAQ GLOBAL"`이면 `KOSDAQ`로 정규화된다.
  - `test_title_beats_orgs`: 제목과 orgs에 모두 있는 회사 → salience 1.0, sources `("orgs", "title")`
  - `test_orgs_only_exact_alias`: orgs의 `"SK하닉"`이 시드 별칭이면 SK하이닉스로 인식된다. `"중국"`, `"정부"`는 무시된다.
  - `test_empty_entities_still_match_title`: 개체명 필드가 빈 기사도 제목 매칭은 동작한다.

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_entities.py -q`
Expected: FAIL

- [ ] **Step 3: 구현과 시드 작성** — `CompanyMatcher`는 `Alias(alias, ticker=<key>, name_official, alias_type, ambiguity, group_id="")`로 만든다.

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_entities.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋** (시드 이름이 KRX 공식명과 맞는지는 Task 10의 `build-master`가 확인한다)

```bash
git add nexus/nexus/cosmos_reuse.py nexus/nexus/entities nexus/nexus/models.py nexus/tests/test_entities.py
git commit -m "feat: 반도체 기업 마스터와 기사별 기업 인식"
```

---

### Task 5: 문장 단위 동시언급과 근거 관계

**Files:**
- Create: `nexus/nexus/relations/__init__.py`, `nexus/nexus/relations/comention.py`, `nexus/nexus/relations/evidence.py`
- Test: `nexus/tests/test_relations.py`

**Interfaces:**
- Consumes: `Article`, `Recognizer.key_of_org` (Task 4), COSMOS `extract_relations.classify(sent, a, b) -> (src_alias, dst_alias, rel_type, confidence, pattern) | None`
- Produces:
  - `@dataclass(frozen=True) PairStat(a: str, b: str, n_ab: int, n_a: int, n_b: int, n_docs: int, npmi: float)`. `a < b` (문자열 순서)
  - `sentence_comentions(articles: list[Article], key_of_org: Callable[[str], str | None], *, min_count: int = 3) -> list[PairStat]`
    - 기사마다 구간별 `OG` 개체를 key로 바꾸고, 한 구간에 함께 나온 쌍을 기사당 한 번만 센다.
    - `n_a`는 a가 어느 구간에든 나온 기사 수, `n_docs`는 전체 기사 수다.
    - `npmi = log(n_ab * n_docs / (n_a * n_b)) / -log(n_ab / n_docs)`. `n_ab == n_docs`이면 1.0.
    - `n_ab >= min_count`인 쌍만 반환하고, `n_ab` 내림차순으로 정렬한다.
  - `models.py`에 추가: `@dataclass(frozen=True) EvidenceSnippet(news_id: str, src: str, dst: str, rel: str, snippet: str, pattern: str, confidence: float, title: str, provider: str, published: datetime, link: str)`
  - `hilight_fragments(hilight: str) -> list[str]`: `<b>`, `</b>` 태그를 지우고 `..` 기준으로 나눈 뒤, 각 조각을 `다.` 뒤에서 다시 나눈다. 앞뒤 공백을 정리하고 빈 조각은 버린다.
  - `extract_evidence(docs: list[dict], a: Company, b: Company, aliases_of: Callable[[str], list[str]]) -> list[EvidenceSnippet]`
    - `docs`는 `pair_hilights` 결과다. 조각마다 a의 별칭 하나와 b의 별칭 하나가 함께 들어 있으면 `classify(frag, x, y)`와 `classify(frag, y, x)`를 차례로 시도하고, 먼저 나온 결과를 쓴다.
    - 결과의 별칭은 회사 key로 되돌린다. `snippet`은 조각 앞 300자까지다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_comention_counts_once_per_article`: 한 기사의 두 구간에 A, B가 같이 나와도 `n_ab == 1`
  - `test_comention_npmi_value`: 기사 10건 중 A가 4건, B가 4건, 같은 구간 동시 출현 3건이면 `npmi`는 `log(3*10/16) / -log(3/10)`과 소수점 6자리까지 같다.
  - `test_comention_ignores_non_og_and_unknown`: `LC`, `PS` 개체와 사전에 없는 기관은 세지 않는다.
  - `test_fragments_strip_tags_and_split`: `"앞 문장..<b>A사</b>는 <b>B사</b>에 납품한다. 뒤"` → `["앞 문장", "A사는 B사에 납품한다.", "뒤"]`
  - `test_extract_supply_direction`: 조각 `"A사는 B사에 장비를 납품한다."` (A, B가 별칭) → `src=A의 key`, `dst=B의 key`, `rel="SUPPLY"`, `pattern="subj-target"`
  - `test_extract_skips_fragment_without_both`: 한 회사만 있는 조각은 무시한다.

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_relations.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_relations.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/relations nexus/nexus/models.py nexus/tests/test_relations.py
git commit -m "feat: 문장 단위 동시언급과 hilight 근거 관계 추출"
```

---

### Task 6: 기준 그래프

**Files:**
- Create: `nexus/nexus/relations/graph.py`
- Test: `nexus/tests/test_graph.py`

**Interfaces:**
- Consumes: `PairStat`, `EvidenceSnippet` (Task 5), `Company` (Task 4)
- Produces:
  - `models.py`에 추가: `@dataclass(frozen=True) Edge(src: str, dst: str, rel: str, strength: float, directed: bool, source: str)`. `source`는 `"COSMOS"`, `"NEWS"`, `"COMENTION"` 중 하나다.
  - `TYPE_WEIGHT = {"SUPPLY": 0.8, "INVEST": 0.6, "COMPETE": 0.5, "PARTNER": 0.5, "CO_MENTIONED": 0.4}`
  - `DIRECTED = {"SUPPLY", "INVEST"}`
  - `load_cosmos_edges(csv_path: Path, keys: set[str]) -> list[Edge]`: COSMOS `AI/graph/data/db_full_20260922/company_relationship.csv`(`source_market,source_stock_code,target_market,target_stock_code,relationship_type_code`)에서 양 끝이 모두 `keys`에 있는 행만 읽는다. strength는 1.0이다.
  - `news_edges(snippets: list[EvidenceSnippet], *, min_strength: float = 0.5) -> list[Edge]`: `(src, dst, rel)`별로 `strength = 1 - Π(1 - confidence)`. 무방향 관계는 `src < dst`로 정렬한 뒤 묶는다.
  - `comention_edges(stats: list[PairStat]) -> list[Edge]`: `npmi > 0`인 쌍. strength는 `npmi`, rel은 `"CO_MENTIONED"`, 무방향이다.
  - `class BaseGraph(edges: list[Edge])`
    - `neighbors(key: str) -> list[tuple[str, Edge]]`: 방향과 무관하게 양쪽으로 순회한다. 전이는 공급사와 고객사 양방향으로 일어나기 때문이다.
    - `weight(edge: Edge) -> float`: `TYPE_WEIGHT[rel] * strength`.
    - 같은 두 노드 사이에 여러 간선이 있으면 `neighbors`는 모두 돌려준다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_cosmos_edges_filtered_by_keys`: 끝 하나가 `keys` 밖인 행은 제외된다.
  - `test_news_edge_strength_combines`: 같은 SUPPLY 근거 confidence 0.7, 0.5 → strength 0.85
  - `test_news_edge_below_min_dropped`: 근거 하나 confidence 0.4 → 간선 없음
  - `test_undirected_merges_both_orders`: PARTNER (A,B), (B,A) 근거는 간선 하나로 합쳐진다.
  - `test_neighbors_traverse_reverse_direction`: SUPPLY A→B가 있으면 `neighbors("B")`에 A가 있다.
  - `test_weight_uses_type_weight`: SUPPLY strength 0.5 → weight 0.4

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_graph.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_graph.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/relations/graph.py nexus/nexus/models.py nexus/tests/test_graph.py
git commit -m "feat: 근거·DART·동시언급 기준 그래프"
```

---

### Task 7: 규칙 기반 추정 전이

**Files:**
- Create: `nexus/nexus/propagation/__init__.py`, `nexus/nexus/propagation/propagate.py`
- Test: `nexus/tests/test_propagate.py`

**Interfaces:**
- Consumes: `BaseGraph`, `Edge` (Task 6), `CompanyMention` (Task 4)
- Produces:
  - `models.py`에 추가: `@dataclass(frozen=True) PathStep(src: str, dst: str, rel: str)`, `@dataclass(frozen=True) Impact(key: str, basis: str, hop: int, path: tuple[PathStep, ...], score: float, status: str, hold_reason: str | None)`. `basis`는 `"REPORTED"`/`"INFERRED"`, `status`는 `"OK"`/`"HOLD"`이다.
  - 상수: `MAX_HOPS = 2`, `OK_THRESHOLD = 0.30`, `HOLD_THRESHOLD = 0.15`, `MAX_INFERRED = 20`
  - `HOLD_LOW = "전이 강도 낮음"`, `HOLD_COMENTION_ONLY = "동시언급만으로 연결"`
  - `propagate(mentions: list[CompanyMention], graph: BaseGraph) -> list[Impact]`
    - 언급 기업은 `REPORTED`, `hop=0`, `path=()`, `score=salience`, `status="OK"`이다.
    - 추정 기업의 점수는 seed에서 출발하는 최대 2단계 경로 중 `salience × Π weight(edge)`의 최댓값이다. seed는 추정 대상에서 뺀다.
    - `path`는 seed에서 바깥쪽으로 가는 순서이고, 각 `PathStep`에는 실제 간선 방향(`edge.src`, `edge.dst`)을 기록한다. 거꾸로 순회했어도 방향을 바꾸지 않는다.
    - 상태 판정 순서: 경로의 모든 간선이 `CO_MENTIONED`이면 `HOLD`(`HOLD_COMENTION_ONLY`). 아니면 `score >= OK_THRESHOLD`이면 `OK`, `HOLD_THRESHOLD <= score < OK_THRESHOLD`이면 `HOLD`(`HOLD_LOW`), 그 미만은 버린다.
    - 추정은 점수 내림차순 상위 `MAX_INFERRED`개만 남긴다. 반환 순서는 REPORTED 먼저(salience 내림차순), 그다음 INFERRED(점수 내림차순)이다. 동점이면 key 오름차순이다.

- [ ] **Step 1: 실패하는 테스트 작성** — 그래프: SUPPLY C→A (strength 1.0), COMPETE A–D (1.0), SUPPLY E→C (1.0), CO_MENTIONED A–F (0.9). seed A(salience 1.0).
  - `test_reported_first`: 첫 Impact는 A, `REPORTED`, `hop=0`
  - `test_one_hop_supply_score`: C의 점수 0.8, `hop=1`, `path == (PathStep("C","A","SUPPLY"),)`, `OK`
  - `test_two_hop_score_and_path`: E의 점수 0.64, `hop=2`, path는 `(PathStep("C","A","SUPPLY"), PathStep("E","C","SUPPLY"))` (seed에서 바깥 방향 순서)
  - `test_comention_only_is_hold`: F는 점수 0.36이어도 `HOLD`, `hold_reason == "동시언급만으로 연결"`
  - `test_low_score_hold_and_drop`: 위 그래프에 COMPETE A–G (strength 0.5), PARTNER A–H (strength 0.2)를 더하면 G(0.25)는 `HOLD`(`HOLD_LOW`), H(0.10)는 결과에 없다.
  - `test_seed_not_inferred`: seed 두 개가 서로 이웃이어도 둘 다 `REPORTED`로만 나온다.
  - `test_cap_max_inferred`: 이웃 30개짜리 별 모양 그래프 → INFERRED는 20개

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_propagate.py -q`
Expected: FAIL

- [ ] **Step 3: 구현** — seed마다 깊이 2까지 BFS로 경로를 펼치고, 노드별로 최고 점수 경로를 남긴다.

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_propagate.py -q`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/propagation nexus/nexus/models.py nexus/tests/test_propagate.py
git commit -m "feat: 규칙 기반 2단계 추정 전이와 판단 보류"
```

---

### Task 8: 주가와 초과수익률

**Files:**
- Create: `nexus/nexus/impact/__init__.py`, `nexus/nexus/impact/prices.py`, `nexus/nexus/impact/abnormal.py`
- Test: `nexus/tests/test_abnormal.py`

**Interfaces:**
- Produces:
  - `BENCHMARK = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}`. 나스닥 기업은 1단계에서 초과수익률을 계산하지 않는다.
  - `class PriceCache(root: Path, fetch: Callable[[str, date, date], pd.DataFrame] | None = None)`. 기본 fetch는 `FinanceDataReader.DataReader(symbol, start, end)`다.
  - `PriceCache.closes(symbol: str, start: date, end: date) -> pd.Series`: 인덱스는 거래일(`date`), 값은 종가다. `root/<symbol>.parquet`에 캐시하고 모자란 기간만 새로 받는다.
  - `MARKET_CLOSE = time(15, 30)`
  - `event_trading_date(dateline: datetime, trading_days: list[date]) -> date | None`: KST 기준으로 출고 시각이 15:30 이후면 출고일 다음 거래일, 아니면 출고일 이상인 첫 거래일이다. 달력 밖이면 None.
  - `models.py`에 추가: `@dataclass(frozen=True) AbnormalReturn(key: str, window_days: int, benchmark: str, stock_return: float, benchmark_return: float, abnormal_return: float)`
  - `abnormal_returns(key: str, t0: date, stock: pd.Series, index: pd.Series, benchmark: str, windows: tuple[int, ...] = (1, 3)) -> list[AbnormalReturn]`
    - 기준가는 t0 전 거래일 종가, 끝값은 t0부터 세어 `window_days`번째 거래일 종가다 (`window_days=1`이면 t0 당일).
    - `stock_return = 끝/기준 - 1`, `benchmark_return`도 같은 식, `abnormal_return = stock_return - benchmark_return`.
    - 기준일이나 끝 거래일 데이터가 없으면 그 창은 만들지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성** — 거래일 2026-10-12(월)~10-16(금), 10-19(월).
  - `test_before_close_same_day`: 10-13 15:29 KST → 10-13
  - `test_after_close_next_day`: 10-13 15:30 KST → 10-14
  - `test_weekend_next_trading_day`: 10-17(토) 10:00 → 10-19
  - `test_ar_window_1_and_3`: 종가 10-12 100, 10-13 110, 10-14 121, 10-15 121. 지수 같은 날 1000, 1010, 1020, 1030. t0=10-13이면 창 1의 AR은 `0.10 - 0.01 = 0.09`, 창 3의 AR은 `0.21 - 0.03 = 0.18`
  - `test_future_window_skipped`: t0가 마지막 거래일이면 창 3은 결과에 없다.
  - `test_price_cache_fetches_missing_only`: 10-12~10-14를 받은 뒤 10-12~10-16을 요청하면 fetch는 10-15~10-16만 요청한다.

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_abnormal.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_abnormal.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/impact nexus/nexus/models.py nexus/tests/test_abnormal.py
git commit -m "feat: 이벤트 거래일 결정과 시장 대비 초과수익률"
```

---

### Task 9: V10 스키마와 PostgreSQL 적재

**Files:**
- Create: `BackEnd/src/main/resources/db/migration/V10__add_nexus_event_tables.sql`, `nexus/nexus/load/__init__.py`, `nexus/nexus/load/postgres.py`
- Modify: `nexus/tests/conftest.py` (임시 DB 픽스처 추가)
- Test: `nexus/tests/test_load.py`

**V10 (트랙 B와의 계약):**

```sql
ALTER TABLE company_relationship
    ADD COLUMN relation_source VARCHAR(20) NOT NULL DEFAULT 'COSMOS',
    ADD CONSTRAINT chk_company_relationship_source
        CHECK (relation_source IN ('COSMOS', 'NEWS', 'DART', 'IO_TABLE'));

CREATE TABLE news_event (
    event_id               UUID PRIMARY KEY,
    document_id            UUID NOT NULL REFERENCES source_document (document_id) ON DELETE CASCADE,
    bigkinds_news_id       VARCHAR(64) NOT NULL UNIQUE,
    value_chain            VARCHAR(20) NOT NULL CHECK (value_chain IN ('SEMICONDUCTOR', 'BATTERY', 'AUTO')),
    categories             TEXT[] NOT NULL DEFAULT '{}',
    incidents              TEXT[] NOT NULL DEFAULT '{}',
    event_type             VARCHAR(50),
    event_type_confidence  NUMERIC(7, 6),
    dateline               TIMESTAMPTZ NOT NULL,
    event_trading_date     DATE,
    issue_cluster_id       VARCHAR(100),
    status                 VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELLED')),
    pipeline_version       VARCHAR(50) NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_news_event_chain_dateline ON news_event (value_chain, dateline DESC);

CREATE TABLE event_company_impact (
    event_id           UUID NOT NULL REFERENCES news_event (event_id) ON DELETE CASCADE,
    company_id         UUID NOT NULL REFERENCES company (company_id),
    basis              VARCHAR(10) NOT NULL CHECK (basis IN ('REPORTED', 'INFERRED')),
    hop                SMALLINT NOT NULL CHECK (hop BETWEEN 0 AND 2),
    path               JSONB NOT NULL DEFAULT '[]',
    propagation_score  NUMERIC(7, 6) NOT NULL,
    impact_direction   VARCHAR(10) CHECK (impact_direction IN ('POSITIVE', 'NEUTRAL', 'NEGATIVE')),
    impact_magnitude   NUMERIC(7, 6),
    confidence         NUMERIC(7, 6),
    status             VARCHAR(10) NOT NULL CHECK (status IN ('OK', 'HOLD')),
    hold_reason        TEXT,
    model_version      VARCHAR(50) NOT NULL,
    PRIMARY KEY (event_id, company_id),
    CONSTRAINT chk_impact_basis_hop CHECK ((basis = 'REPORTED' AND hop = 0) OR (basis = 'INFERRED' AND hop > 0)),
    CONSTRAINT chk_impact_hold_reason CHECK (status = 'OK' OR hold_reason IS NOT NULL)
);
CREATE INDEX ix_event_company_impact_company ON event_company_impact (company_id, basis);

CREATE TABLE event_abnormal_return (
    event_id          UUID NOT NULL REFERENCES news_event (event_id) ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES company (company_id),
    window_days       SMALLINT NOT NULL CHECK (window_days IN (1, 3)),
    benchmark         VARCHAR(20) NOT NULL,
    stock_return      NUMERIC(12, 8) NOT NULL,
    benchmark_return  NUMERIC(12, 8) NOT NULL,
    abnormal_return   NUMERIC(12, 8) NOT NULL,
    PRIMARY KEY (event_id, company_id, window_days)
);

CREATE TABLE relation_evidence_snippet (
    snippet_id            UUID PRIMARY KEY,
    source_company_id     UUID NOT NULL REFERENCES company (company_id),
    target_company_id     UUID NOT NULL REFERENCES company (company_id),
    relationship_type_id  SMALLINT NOT NULL REFERENCES relationship_type (relationship_type_id),
    bigkinds_news_id      VARCHAR(64) NOT NULL,
    title                 TEXT NOT NULL,
    provider              VARCHAR(200),
    published_at          TIMESTAMPTZ NOT NULL,
    original_url          TEXT NOT NULL DEFAULT '',
    snippet               VARCHAR(300) NOT NULL,
    pattern               VARCHAR(30) NOT NULL,
    confidence            NUMERIC(7, 6) NOT NULL,
    CONSTRAINT uk_relation_evidence_snippet UNIQUE (bigkinds_news_id, source_company_id, target_company_id, relationship_type_id)
);
CREATE INDEX ix_relation_evidence_pair ON relation_evidence_snippet (source_company_id, target_company_id, relationship_type_id);
```

`path` JSON 형식: `[{"src": "KOSPI:000660", "dst": "KOSPI:005930", "rel": "SUPPLY"}]` (key 문자열, 실제 간선 방향).

**Interfaces:**
- Consumes: `Article`, `Company`, `CompanyMention`, `Impact`, `AbnormalReturn`, `EvidenceSnippet`, `Edge`
- Produces:
  - `NAMESPACE = uuid.NAMESPACE_URL`, `document_id(news_id) = uuid5(NAMESPACE, "bigkinds:document:" + news_id)`, `event_id(news_id) = uuid5(NAMESPACE, "bigkinds:event:" + news_id)`, `snippet_id(s) = uuid5(NAMESPACE, f"bigkinds:snippet:{news_id}:{src}:{dst}:{rel}")`
  - `PIPELINE_VERSION = "nexus-slice-v1"`, `MODEL_VERSION = "rule-propagation-v1"`
  - `@dataclass EventBundle(article: Article, value_chain: str, mentions: list[CompanyMention], impacts: list[Impact], returns: list[AbnormalReturn], trading_date: date | None)`
  - `ensure_companies(conn, companies: list[Company]) -> dict[str, UUID]`: `(market, stock_code)`로 기존 행을 찾고 없으면 넣는다. key → company_id를 반환한다.
  - `load_events(conn, bundles: list[EventBundle], company_ids: dict[str, UUID]) -> int`. 한 트랜잭션에서 순서대로:
    1. `data_source(name='bigkinds', source_type='NEWS_API')` 없으면 넣기
    2. `source_document` upsert (`document_type='NEWS'`, `title`, `summary=content`, `original_url=link`, `published_at=dateline`, `status='ANALYZED'`, `analysis_version=PIPELINE_VERSION`)
    3. `news_article` upsert (`canonical_url = "bigkinds://news/" + news_id`, `canonical_url_hash = sha256(canonical_url)`, `publisher=provider`, `author=byline`)
    4. 언급 기업마다 `company_document` upsert (`mention_type='REPORTED'`, `relevance_score=salience`, `is_service_visible=TRUE`, `model_version=PIPELINE_VERSION`). 기존 `/api/news`와 기업 패널 뉴스에 바로 나타난다.
    5. `news_event` upsert
    6. 해당 이벤트의 `event_company_impact`, `event_abnormal_return`을 지우고 다시 넣기
  - `load_relations(conn, edges: list[Edge], snippets: list[EvidenceSnippet], company_ids: dict[str, UUID]) -> int`: `source="NEWS"` 간선을 `company_relationship`에 `relation_source='NEWS'`로 넣고(이미 있으면 그대로 둠), 근거 조각을 `relation_evidence_snippet`에 upsert한다.
  - `load_prices(conn, company_ids: dict[str, UUID], bars_by_key: dict[str, pd.DataFrame]) -> int`: `bars_by_key`의 DataFrame은 FinanceDataReader 형식(`Open`, `High`, `Low`, `Close`, `Volume`, 날짜 인덱스)이다. `stock_price_history`에 `interval_type='1D'`, `trading_at`은 그 날짜 00:00 KST로 넣고 `ON CONFLICT DO NOTHING`
  - `ensure_relationship_types(conn) -> None`: `relationship_type`에 `SUPPLY`(DIRECTED), `INVEST`(DIRECTED), `PARTNER`(UNDIRECTED), `COMPETE`(UNDIRECTED)를 `ON CONFLICT (code) DO NOTHING`으로 넣는다. COSMOS는 이 행을 마이그레이션이 아닌 SQL 패키지로 넣었기 때문에 빈 DB에는 없다.
  - `apply_migrations_for_tests(conn, migration_dir: Path) -> None`: `V<n>__*.sql`을 n 순서로 실행한다. **테스트용 임시 DB에만 쓴다.** 실제 `cosmos` DB는 Flyway로 적용한다 (Task 10).
- `conftest.py`의 `pg` 픽스처: 환경변수 `TEST_DATABASE_URL`(예: `postgresql://cosmos:cosmos1234@localhost:5432/postgres`)이 없으면 skip한다. 있으면 `nexus_test_<uuid8>` 데이터베이스를 만들고, `apply_migrations_for_tests`와 `ensure_relationship_types`를 실행한 뒤 연결을 넘기고, 끝나면 데이터베이스를 지운다.

- [ ] **Step 1: 실패하는 테스트 작성** (`pg` 픽스처 사용)
  - `test_v10_applies_after_v9`: 픽스처가 V1~V10을 오류 없이 적용하고 `news_event` 테이블이 있다.
  - `test_load_events_writes_chain`: 이벤트 1건(언급 2개, 추정 1개, AR 2개)을 적재하면 `source_document`, `news_article`, `company_document` 2행, `news_event` 1행, `event_company_impact` 3행, `event_abnormal_return` 2행이 있다.
  - `test_reload_is_idempotent`: 같은 묶음을 두 번 적재해도 행 수가 같다.
  - `test_inferred_row_has_path_and_no_document`: INFERRED 행의 `path`가 `[{"src": …, "dst": …, "rel": "SUPPLY"}]`이고, 그 회사의 `company_document` 행은 없다.
  - `test_hold_requires_reason`: `status='HOLD'`, `hold_reason=NULL` 직접 삽입은 CHECK 위반이다.
  - `test_news_visible_in_existing_query`: `company_document.is_service_visible = TRUE`이고 `company.status = 'ACTIVE'`인 조인이 1행 이상이다 (기존 `/api/news` 조건).

- [ ] **Step 2: DB 띄우고 실패 확인**

Run: `docker compose -f BackEnd/docker-compose.yml up -d postgres` 후 `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_load.py -q`
Expected: FAIL (V10 없음 또는 `nexus.load` 없음)

- [ ] **Step 3: V10과 `postgres.py` 구현**

- [ ] **Step 4: 통과 확인**

Run: 위와 같음
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add BackEnd/src/main/resources/db/migration/V10__add_nexus_event_tables.sql nexus/nexus/load nexus/tests/conftest.py nexus/tests/test_load.py
git commit -m "feat: V10 이벤트 테이블과 PostgreSQL 적재"
```

---

### Task 10: 파이프라인 CLI와 반도체 슬라이스 실제 실행

**Files:**
- Create: `nexus/nexus/pipeline.py`, `nexus/nexus/__main__.py`
- Test: `nexus/tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1~9의 모든 공개 함수
- Produces: `python -m nexus <command>`
  - `build-master --chain semis`: 시드 + KRX 목록 + COSMOS `companies.csv`·`aliases.csv` → `nexus/data/master/semis_companies.csv`, `semis_aliases.csv`. 미해결 이름이 있으면 출력하고 종료 코드 1.
  - `collect --chain semis --start 2025-01-01 --end <오늘>`: `category=["경제>반도체"]`로 월 단위 파티션 수집 → `nexus/data/articles/partition=semis-YYYY-MM/`. 진행 상황(월, 건수)을 출력한다.
  - `analyze --chain semis`: `build_graph` → `run_analyze` → `nexus/data/analyzed/semis/events.pkl`, `relations.pkl`(간선과 근거 조각), `prices.pkl`(마스터 국내 기업 일봉).
  - `load --chain semis --database-url <url>`: `ensure_relationship_types` → `ensure_companies` → `load_relations` → `load_events` → `load_prices`.
  - `build_graph(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company], client: BigKindsClient, *, start: date, end: date, today: date, cosmos_csv: Path, top_pairs: int = 300) -> tuple[BaseGraph, list[Edge], list[EvidenceSnippet]]`: 동시언급(`min_count=3`) → `n_ab` 상위 `top_pairs`쌍에 `pair_hilights`(공식명으로 검색) → `extract_evidence` → `news_edges` + `comention_edges` + `load_cosmos_edges`. 네트워크를 쓰는 유일한 분석 단계다.
  - `run_analyze(articles: list[Article], recognizer: Recognizer, companies: dict[str, Company], graph: BaseGraph, prices: PriceCache, *, value_chain: str) -> list[EventBundle]`: 네트워크 없이 테스트할 수 있게 분리한 핵심이다. 이벤트는 해당 밸류체인 마스터 기업이 하나 이상 인식된 기사다. 거래일 달력은 `prices.closes("KS11", …)`의 인덱스로 만든다. 초과수익률은 KOSPI·KOSDAQ 기업에만 계산한다.
  - `migrate --database-url <jdbc가 아닌 postgresql url>`: 실제 DB에 Flyway 도커 이미지로 V1~V10을 적용한다. 실행 명령: `docker run --rm -v <repo>/BackEnd/src/main/resources/db/migration:/flyway/sql flyway/flyway:11 -url=jdbc:postgresql://host.docker.internal:5432/cosmos -user=cosmos -password=cosmos1234 migrate`. Flyway로 적용해야 `flyway_schema_history`가 생겨서, 나중에 트랙 B가 Spring Boot를 띄울 때 그대로 검증을 통과한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_run_analyze_end_to_end_synthetic`: 합성 기사 4건 + 합성 마스터 + 손으로 만든 `BaseGraph` + 가짜 fetch를 넣은 `PriceCache`로 `run_analyze`를 돌리면, 기업이 인식된 기사마다 EventBundle이 하나씩 나오고 REPORTED impact가 먼저 온다. 15:40 출고 기사의 `trading_date`는 다음 거래일이다.
  - `test_articles_without_master_company_are_not_events`

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_pipeline.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

- [ ] **Step 4: 전체 테스트 통과 확인**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`
Expected: 모든 테스트 통과 (Task 1~10 합계 61개)

- [ ] **Step 5: 실제 실행** (빅카인즈 호출 발생)

```bash
nexus/.venv/Scripts/python -X utf8 -m nexus build-master --chain semis
nexus/.venv/Scripts/python -X utf8 -m nexus collect --chain semis --start 2025-01-01 --end 2026-10-14
nexus/.venv/Scripts/python -X utf8 -m nexus analyze --chain semis
nexus/.venv/Scripts/python -X utf8 -m nexus migrate --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos
nexus/.venv/Scripts/python -X utf8 -m nexus load --chain semis --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos
```

Expected: 수집 약 35만 건 (실측 문서 5절 기준), 이벤트 수와 INFERRED 행 수 출력, 적재 오류 없음. 이어서 다음 질의가 0보다 큰 값을 돌려준다:

```sql
SELECT basis, status, count(*) FROM event_company_impact GROUP BY 1, 2;
SELECT count(*) FROM event_abnormal_return;
```

- [ ] **Step 6: 결과 기록과 커밋** — `docs/nexus/slice1-run.md`에 실행 날짜, 수집 건수, 이벤트 수, REPORTED/INFERRED/HOLD 수, 호출 수, 소요 시간, 호출 제한 관찰 여부를 적는다. 기사 원문은 적지 않는다.

```bash
git add nexus/nexus/pipeline.py nexus/nexus/__main__.py nexus/tests/test_pipeline.py docs/nexus/slice1-run.md
git commit -m "feat: 반도체 슬라이스 파이프라인 CLI와 첫 실행 결과"
```

---

## 계획 범위 밖 (다음 계획)

- **계획 2 (트랙 B):** JDK 21, 백엔드 `event` 도메인 API(`/api/events`, `/api/events/{id}/map`, 근거 조회), 원형 2겹 관계 지도, 근거 패널. 이 계획 Task 9의 V10이 입력 계약이다.
- **2단계 이후:** KPF-BERT 관계 판별기·이벤트 분류기, 영향 모델(방향·크기), 실시간 수집, 유사 사례, 2차전지·자동차 확장, 알림.
