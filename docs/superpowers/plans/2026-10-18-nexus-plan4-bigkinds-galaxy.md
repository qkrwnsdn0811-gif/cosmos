# NEXUS 계획 4: 빅카인즈 은하 발행과 국내 상장사 범위 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 빅카인즈 기사에서 나온 관계만으로 은하 그래프를 발행하고, 서비스 전체를 국내 상장사(KOSPI·코스닥)로 맞춘다.

**Architecture:** 기업 사전 단계에서 시장을 걸러 분석부터 다시 돌린다. 새 패키지 `nexus/nexus/galaxy/`가 기간별 간선·점수·근거 기사를 계산하고(순수 함수), `nexus/nexus/load/galaxy.py`가 V11 이후 스키마에 한 트랜잭션으로 발행한다. 백엔드는 은하 기본 시장만, 프론트는 함께 언급 유형 표시만 고친다.

**Tech Stack:** Python 3.12, pandas, psycopg 3, pytest / Spring Boot 4.1.1, Java 21, JUnit 5 / React 19, TypeScript, three.js, Node `node --test`

**Spec:** `docs/superpowers/specs/2026-10-18-nexus-bigkinds-galaxy-design.md`

## Global Constraints

- 파이썬 명령은 저장소 루트에서 `nexus/.venv/Scripts/python -X utf8 ...`. 전체 테스트: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q` (DB 테스트는 이 값이 있어야 돈다).
- 백엔드 테스트: `cd BackEnd && JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-21.0.12.101-hotspot" DB_URL=jdbc:postgresql://localhost:5432/cosmos_test REDIS_PORT=6380 ./gradlew test`.
- 프론트 검증: `cd FrontEnd && npm test && npm run typecheck && npm run lint`.
- DB·Redis는 `docker start cosmos-postgres cosmos-redis-6380`으로 켠다. 작업이 끝나면 띄운 서버와 컨테이너를 모두 닫는다(사용자 요청).
- 스펙 고정값:
  - 국내 시장 `("KOSPI", "KOSDAQ")`. 시드 파일의 나스닥 행은 지우지 않는다.
  - 기간 `WINDOWS = {"30D": 30, "1Y": 365, "10Y": None}`(None = 전체). 기간은 `[as_of − n일, as_of)`.
  - `as_of` = (모든 밸류체인 수집 기사 출고일(KST)의 최대값 + 1일) 00:00 KST.
  - 함께 언급: 동시언급 기사 3건 이상, NPMI > 0, 기간마다 기업당 상위 5개(동시언급 기사 수 내림차순, 같으면 NPMI 내림차순, 같으면 상대 기업 키 오름차순)의 합집합. 점수 `60 × ln(1+n) / ln(1+그 기간 선택된 쌍의 최대 n)`. 근거 기사는 같은 문장에 함께 나온 기간 안 최근 기사 최대 20건.
  - 근거 관계: 기간 안 근거 조각이 1개 이상. 점수 `60 + 10 × min(기간 안 조각 수, 4)`, 신뢰도 = 조각 신뢰도 최대값. 근거 기사 = 기간 안 근거 조각 기사(문장 포함).
  - 점수는 `score`와 `news_score`에 같은 값, `disclosure_score`·`impact_direction`은 NULL, 함께 언급의 `confidence`는 NULL. 소수 여섯째 자리 반올림.
  - `evidence_count` = 그 기간에 연결한 근거 기사 수.
  - 스냅샷: `formula_version = 'nexus-bigkinds-v1'`, `model_version = 'nexus-slice-v1'`, `hdfs_uri = 'bigkinds://nexus/<as_of 날짜 YYYY-MM-DD>'`, `status = 'PUBLISHED'`. 다른 `PUBLISHED` 스냅샷은 `SUPERSEDED`.
  - 근거 행의 `model_version`은 `relationship_evidence`·`document_evidence`·`company_document` 모두 `'nexus-graph-v1'`.
  - 업종 이름 `"<밸류체인 라벨> <역할>"`(예: "반도체 장비"). 반도체 라벨은 `"반도체"`.
  - 관계 유형 `CO_MENTIONED`(이름 "함께 언급", `UNDIRECTED`), 관계 출처 `COMENTION`.
  - 프론트 라벨 "함께 언급", 색 `#8A93A3`, 설명 "같은 문장에 함께 나온 기사 수 기반 · 관계의 근거는 아님", 흐림 계수 0.45.
- V10 마이그레이션을 고치지 않는다. 스키마 변경은 V11 하나.
- 커밋은 작업마다 로컬에만. Claude 표기 금지. push하지 않는다.

## Review Focus

1. **기간 안에 간선이 하나도 없음**(예: 30일 안에 동시언급 3건 넘는 쌍이 없음) → 발행은 성공하고 그 기간은 간선 없는 행성만 보여야 한다. (Task 2, Task 3 테스트)
2. **발행 뒤 `load` 재실행** → 근거 문장이 걸린 문서-기업 행을 `load`가 지워도 외래키 오류 없이 끝나야 한다. (Task 3 테스트)
3. **무방향 관계가 반대 방향으로 이미 저장됨**(COSMOS CSV 방향을 따른 `load_relations` 행) → 발행이 그 행을 다시 쓰고 중복 관계를 만들지 않아야 한다. (Task 3 테스트)
4. **근거 조각 기사가 DB에 없음**(기업쌍 검색에서만 나온 기사) → 문서를 추가하고 `evidence_count`가 실제 연결 수와 같아야 한다. (Task 3 테스트)
5. **기존 주 업종이 있는 기업** → 주 업종을 덮어쓰지 않아야 한다. (Task 3 테스트)

---

## 파일 구조

```text
nexus/nexus/entities/master.py          build_master(..., markets=) 시장 필터
nexus/nexus/pipeline.py                 CHAINS[...]["markets"/"label"], cmd_build_master, cmd_publish_graph
nexus/nexus/galaxy/__init__.py, build.py 기간별 간선·점수·근거 기사 계산 (순수)
nexus/nexus/load/galaxy.py              publish_galaxy: 업종, 관계, 스냅샷, 점수, 근거 기사
nexus/nexus/load/postgres.py            load_events/prune_events: 문서-기업 행 삭제 전 관계 근거 정리
BackEnd/src/main/resources/db/migration/V11__allow_comention_relation_source.sql
BackEnd/.../graph/service/GraphService.java   기본 universe KOSPI100,KOSDAQ
FrontEnd/src/lib/edgePick.ts            기업쌍 간선 고르기, CO_MENTIONED 상수 (순수)
FrontEnd/src/lib/meta.ts, lib/graph.ts, store/galaxy.ts, features/galaxy/ControlDock.tsx,
FrontEnd/src/three/emphasis.ts, three/RelationLines.tsx
```

---

### Task 1: 기업 사전을 국내 상장사로

**Files:**
- Modify: `nexus/nexus/entities/master.py`, `nexus/nexus/pipeline.py` (`CHAINS`, `cmd_build_master`)
- Test: `nexus/tests/test_entities.py`

**Interfaces:**
- Produces:
  - `build_master(seed_rows, listing, cosmos_companies, value_chain, markets: tuple[str, ...] | None = None) -> tuple[list[Company], list[str]]`: `markets`가 주어지면 `market_hint`가 그 밖인 시드는 해석하지 않고 건너뛰며(미해결 목록에도 넣지 않는다), 해석 결과 시장이 그 밖인 기업도 버린다.
  - `CHAINS["semis"]`에 `"markets": ("KOSPI", "KOSDAQ")`, `"label": "반도체"`. `cmd_build_master`는 `markets=cfg["markets"]`를 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성** (`test_entities.py`에 추가. 기존 `test_build_master_resolves_krx_and_nasdaq`의 `seed`·`listing`·`cosmos` 만드는 방식을 그대로 쓴다)

```python
def test_build_master_keeps_only_requested_markets():
    seed = [{"name": "삼성전자", "market_hint": "KOSPI", "role": "제조"},
            {"name": "하나마이크론", "market_hint": "KOSDAQ", "role": "후공정"},
            {"name": "Nvidia", "market_hint": "NASDAQ", "role": "설계"}]
    listing = pd.DataFrame([{"Name": "삼성전자", "Market": "KOSPI", "Code": "005930"},
                            {"Name": "하나마이크론", "Market": "KOSDAQ GLOBAL", "Code": "067310"}])
    cosmos = [{"name_official": "Nvidia", "market": "NASDAQ", "ticker": "NVDA"}]
    companies, unresolved = build_master(seed, listing, cosmos, "SEMICONDUCTOR", markets=("KOSPI", "KOSDAQ"))
    assert [c.key for c in companies] == ["KOSPI:005930", "KOSDAQ:067310"]
    assert unresolved == []
    assert len(build_master(seed, listing, cosmos, "SEMICONDUCTOR")[0]) == 3      # markets 없으면 기존 동작
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_entities.py -q`
Expected: FAIL (`unexpected keyword argument 'markets'`)

- [ ] **Step 3: 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_entities.py -q`
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/entities/master.py nexus/nexus/pipeline.py nexus/tests/test_entities.py
git commit -m "feat: 기업 사전을 국내 상장사(KOSPI·코스닥)로 제한"
```

---

### Task 2: 은하 간선 계산 (순수 함수)

**Files:**
- Create: `nexus/nexus/galaxy/__init__.py`, `nexus/nexus/galaxy/build.py`
- Test: `nexus/tests/test_galaxy_build.py`

**Interfaces:**
- Consumes: `nexus.relations.comention.sentence_comentions`, `nexus.models` (`Article`, `Company`, `Edge`, `EvidenceSnippet`)
- Produces (`build.py`):
  - `WINDOWS = {"30D": 30, "1Y": 365, "10Y": None}`, `TOP_PER_COMPANY = 5`, `MIN_COMENTION = 3`, `MAX_DOCS = 20`, `DOMESTIC = ("KOSPI", "KOSDAQ")`
  - `@dataclass(frozen=True) GalaxyDoc(news_id: str, title: str, provider: str, published: datetime, link: str, sentence: str | None)`
  - `@dataclass(frozen=True) GalaxyEdge(src: str, dst: str, rel: str, source: str, window: str, score: float, confidence: float | None, docs: tuple[GalaxyDoc, ...])` — `rel`은 `SUPPLY|PARTNER|COMPETE|CO_MENTIONED`, `source`는 `NEWS|COMENTION`. 무방향(`PARTNER`, `COMPETE`, `CO_MENTIONED`)은 `src < dst`(기업 키 문자열 순), `SUPPLY`는 공급자 → 고객.
  - `as_of_for(articles: Iterable[Article]) -> datetime`: 스펙의 `as_of`(KST aware).
  - `in_window(when: datetime, as_of: datetime, days: int | None) -> bool`
  - `comention_edges(articles, key_of_org, companies: dict[str, Company], as_of) -> list[GalaxyEdge]`: 기간마다 그 기간 기사로 `sentence_comentions(min_count=MIN_COMENTION)`를 다시 계산하고, 양 끝이 `companies` 안의 국내 기업인 쌍만 남긴 뒤 스펙의 상위 5개 규칙과 점수를 적용한다. 근거 기사는 같은 세그먼트에 두 기업이 함께 나온 기간 안 기사를 출고 시각 내림차순으로 최대 20건(`sentence=None`).
  - `evidence_edges(edges: list[Edge], snippets: list[EvidenceSnippet], companies, as_of) -> list[GalaxyEdge]`: `source == "NEWS"`인 간선마다, 같은 관계의 조각(`SUPPLY`는 방향까지, 나머지는 기업쌍 무방향으로 일치) 중 기간 안의 것으로 점수·신뢰도·근거 기사(`sentence = snippet.snippet`, 같은 기사는 한 번)를 만든다.
  - `build_galaxy(chains: list[tuple[list[Article], dict[str, Company], Callable[[str], str | None], list[Edge], list[EvidenceSnippet]]]) -> tuple[datetime, list[GalaxyEdge]]`: 모든 밸류체인 기사로 `as_of`를 정하고, 밸류체인마다 두 함수를 돌려 합친다. 같은 (src, dst, rel, window)가 두 밸류체인에서 나오면 점수가 큰 쪽을 남긴다.

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_galaxy_build.py`)

```python
from datetime import datetime, timedelta, timezone

import pytest

from nexus.galaxy.build import as_of_for, comention_edges, evidence_edges, in_window
from nexus.models import Article, Company, Edge, EvidenceSnippet

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 10, 14, tzinfo=KST)


def co(key, name, market="KOSDAQ"):
    return Company(key, market, key.split(":")[1], name, "SEMICONDUCTOR", "장비")


A, B, C, N = co("KOSPI:000001", "가전자", "KOSPI"), co("KOSDAQ:000002", "나장비"), co("KOSDAQ:000003", "다소재"), \
    co("NASDAQ:NV", "엔비", "NASDAQ")


def art(news_id, at, segments):
    return Article(news_id, "제목", "본문", at.date(), at, "테스트일보", "", ("경제>반도체",), (), "", (), segments, ())


def pair_arts(x, y, n, days_ago=1, tag=""):
    """n articles where companies x and y share one segment, newest first a minute apart."""
    return [art(f"{tag}{x.key}-{y.key}-{i}", AS_OF - timedelta(days=days_ago, minutes=i),
                (((x.name, "OG"), (y.name, "OG")),)) for i in range(n)]


def setup(*companies):
    by_key = {c.key: c for c in companies}
    names = {c.name: c.key for c in companies}
    return by_key, names.get


def test_as_of_is_day_after_last_release_kst():
    assert as_of_for([art("1", datetime(2026, 10, 13, 23, 30, tzinfo=KST), ())]) == AS_OF


def test_window_bounds():
    assert in_window(AS_OF - timedelta(days=30), AS_OF, 30) and not in_window(AS_OF, AS_OF, 30)
    assert not in_window(AS_OF - timedelta(days=30, seconds=1), AS_OF, 30)
    assert in_window(datetime(2020, 1, 1, tzinfo=KST), AS_OF, None)


def test_comention_needs_three_articles_and_scores_up_to_sixty():
    companies, key_of = setup(A, B, C)
    arts = pair_arts(A, B, 4) + pair_arts(A, C, 3) + pair_arts(B, C, 2)
    edges = [e for e in comention_edges(arts, key_of, companies, AS_OF) if e.window == "30D"]
    assert {frozenset((e.src, e.dst)) for e in edges} == {frozenset((A.key, B.key)), frozenset((A.key, C.key))}
    assert max(e.score for e in edges) == pytest.approx(60.0) and all(0 < e.score <= 60 for e in edges)
    assert all(e.rel == "CO_MENTIONED" and e.source == "COMENTION" and e.confidence is None and e.src < e.dst
               for e in edges)


def test_comention_keeps_top_five_per_company():
    bs = [co(f"KOSDAQ:1000{i}", f"비{i}") for i in range(1, 8)]
    ds = [co(f"KOSDAQ:2000{j}", f"디{j}") for j in range(1, 6)]
    companies, key_of = setup(A, *bs, *ds)
    arts = [a for i, b in enumerate(bs, 1) for a in pair_arts(A, b, i + 2)]          # A–B1..B7: 3..9건
    arts += [a for b in bs for d in ds for a in pair_arts(b, d, 10, tag=b.key)]        # B마다 더 강한 상대 5개
    tenY = [e for e in comention_edges(arts, key_of, companies, AS_OF) if e.window == "10Y"]
    partners = {({e.src, e.dst} - {A.key}).pop() for e in tenY if A.key in (e.src, e.dst)}
    assert partners == {b.key for b in bs[2:]}                                          # A 쪽 상위 5개(B3..B7)


def test_comention_drops_non_domestic_pairs():
    companies, key_of = setup(A, N)
    assert comention_edges(pair_arts(A, N, 5), key_of, companies, AS_OF) == []


def test_comention_docs_are_recent_and_capped():
    companies, key_of = setup(A, B)
    e = next(e for e in comention_edges(pair_arts(A, B, 25), key_of, companies, AS_OF) if e.window == "30D")
    assert len(e.docs) == 20 and all(d.sentence is None for d in e.docs)
    assert [d.published for d in e.docs] == sorted((d.published for d in e.docs), reverse=True)


def test_evidence_edge_scores_and_docs():
    companies, _ = setup(A, B)
    src, dst = sorted((A.key, B.key))
    snip = lambda nid, days, conf: EvidenceSnippet(nid, dst, src, "PARTNER", f"문장 {nid}", "enum+kw", conf, "제목",
                                                   "테스트일보", AS_OF - timedelta(days=days), "https://x/" + nid)
    snippets = [snip("n1", 1, 0.7), snip("n1", 1, 0.7), snip("n2", 2, 0.9), snip("n3", 3, 0.6),
                snip("n4", 4, 0.8), snip("old", 40, 0.95)]
    edges = evidence_edges([Edge(src, dst, "PARTNER", 0.85, False, "NEWS")], snippets, companies, AS_OF)
    e30 = next(e for e in edges if e.window == "30D")
    assert e30.score == pytest.approx(100.0)                    # 조각 5개 → 60 + 10 × 4
    assert e30.confidence == pytest.approx(0.9)
    assert sorted(d.news_id for d in e30.docs) == ["n1", "n2", "n3", "n4"] and all(d.sentence for d in e30.docs)
    assert next(e for e in edges if e.window == "1Y").confidence == pytest.approx(0.95)


def test_empty_window_is_not_an_error():
    companies, key_of = setup(A, B)
    edges = comention_edges(pair_arts(A, B, 4, days_ago=40), key_of, companies, AS_OF)
    assert not [e for e in edges if e.window == "30D"] and [e for e in edges if e.window == "1Y"]
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_galaxy_build.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.galaxy`)

- [ ] **Step 3: `build.py` 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_galaxy_build.py -q`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/galaxy nexus/tests/test_galaxy_build.py
git commit -m "feat: 빅카인즈 은하 간선 계산 (근거 관계·함께 언급, 기간별 점수·근거 기사)"
```

---

### Task 3: V11과 은하 발행 DB 반영

**Files:**
- Create: `BackEnd/src/main/resources/db/migration/V11__allow_comention_relation_source.sql`, `nexus/nexus/load/galaxy.py`
- Modify: `nexus/nexus/load/postgres.py` (`load_events`, `prune_events`)
- Test: `nexus/tests/test_load_galaxy.py`

**Interfaces:**
- Consumes: Task 2 `GalaxyEdge`, `GalaxyDoc`, `WINDOWS`; `postgres.document_id`, `_source_id`, `_relationship_type_ids`, `ensure_companies`, `load_events`, `load_relations`
- Produces:
  - V11: `chk_company_relationship_source`를 지우고 같은 이름으로 `relation_source IN ('COSMOS', 'NEWS', 'DART', 'IO_TABLE', 'COMENTION')`을 다시 건다.
  - `galaxy.FORMULA_VERSION = "nexus-bigkinds-v1"`, `GRAPH_MODEL_VERSION = "nexus-graph-v1"`
  - `publish_galaxy(conn, as_of: datetime, edges: list[GalaxyEdge], company_ids: dict[str, UUID], roles: dict[str, tuple[str, str]]) -> dict[str, int]`: `roles`는 기업 키 → (밸류체인 라벨, 역할). 한 트랜잭션에서 스펙 §5.2 순서대로 반영하고 `{"snapshot_rows": …, "edges_30D": …, "edges_1Y": …, "edges_10Y": …, "evidence_rows": …}`를 돌려준다.
    1. `CO_MENTIONED` 관계 유형을 없으면 추가.
    2. 업종 `"<라벨> <역할>"`을 없으면 만들고 `company_industry`에 `(기업, 업종, is_primary = 그 기업에 주 업종이 없으면 TRUE)`를 `ON CONFLICT DO NOTHING`.
    3. 우리 근거 행 삭제: `relationship_evidence` → `document_evidence` → `company_document` (모두 `model_version = 'nexus-graph-v1'`).
    4. 관계 행: 간선마다 같은 유형의 (src, dst) 행을 찾고, 무방향이면 (dst, src) 행도 찾는다. 없으면 넣는다(함께 언급은 `relation_source = 'COMENTION'`, 근거 관계는 `'NEWS'`). 이번 간선에 없는 `COMENTION` 관계 행은 지운다.
    5. 스냅샷: 같은 `(as_of_at, formula_version)` 행이 있으면 재사용(필드 갱신), 없으면 넣는다. 그 밖의 `PUBLISHED` 행은 `SUPERSEDED`.
    6. `relationship_score_current` 전부 삭제 후 삽입.
    7. 근거 기사: 문서가 없으면 `source_document`(`document_type='NEWS'`, `status='COLLECTED'`, `published_at = doc.published`, `original_url = doc.link`, 링크가 비면 `"bigkinds://news/<news_id>"`)와 `news_article`을 `ON CONFLICT DO NOTHING`으로 넣는다. 문장이 있는 문서는 `company_document(문서, 간선 src 기업, model_version='nexus-graph-v1', is_service_visible=FALSE)`를 `ON CONFLICT DO NOTHING`, `document_evidence`(문장, `sentence_order = 0`, 모델 버전 동일)를 넣고 그 `evidence_id`로, 문장 없는 문서는 `evidence_id = NULL`로 `relationship_evidence`를 넣는다. 한 관계에 여러 기간이 같은 문서를 가리키면 근거 행은 한 번만 넣는다.
  - `load_events`와 `prune_events`: `company_document`를 지우기 직전에 `DELETE FROM relationship_evidence WHERE evidence_id IN (SELECT evidence_id FROM document_evidence WHERE document_id = ANY(%s))`를 실행한다(그 문장에 걸린 은하 근거를 먼저 정리해 외래키 오류를 막는다. 다음 `publish-graph`가 다시 채운다).

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_load_galaxy.py`, `pg` 픽스처 사용. `apply_migrations_for_tests`가 V11까지 적용한다)

```python
from datetime import datetime, timedelta, timezone

from nexus.galaxy.build import GalaxyDoc, GalaxyEdge
from nexus.ingest.normalize import to_article
from nexus.load.galaxy import publish_galaxy
from nexus.load.postgres import document_id, ensure_companies, load_events, load_relations
from nexus.models import Company, CompanyMention, Edge, EventBundle, Impact

KST = timezone(timedelta(hours=9))
AS_OF = datetime(2026, 10, 14, tzinfo=KST)
A = Company("KOSPI:900001", "KOSPI", "900001", "가상전자", "SEMICONDUCTOR", "제조")
B = Company("KOSDAQ:900002", "KOSDAQ", "900002", "하늘반도체", "SEMICONDUCTOR", "장비")
C = Company("KOSDAQ:900003", "KOSDAQ", "900003", "바다소재", "SEMICONDUCTOR", "소재")
ROLES = {c.key: ("반도체", c.role) for c in (A, B, C)}
LOADED = "SYN.20261013100000001"          # synthetic_docs[0], load_events로 미리 적재
MISSING = "SYN.NOT.LOADED"


def q(conn, sql, *params):
    return conn.execute(sql, params).fetchall()


def doc(news_id, sentence=None):
    return GalaxyDoc(news_id, "제목", "테스트일보", AS_OF - timedelta(days=1), "", sentence)


def edges(with_comention=True):
    ab = [GalaxyEdge(A.key, B.key, "PARTNER", "NEWS", w, s, 0.8, (doc(LOADED, "협력 문장"), doc(MISSING, "또 다른 문장")))
          for w, s in (("30D", 80.0), ("1Y", 90.0), ("10Y", 90.0))]
    bc = [GalaxyEdge(B.key, C.key, "CO_MENTIONED", "COMENTION", w, 40.0, None, (doc(LOADED),))
          for w in ("1Y", "10Y")]
    return ab + (bc if with_comention else [])


def seed(pg, synthetic_docs):
    ids = ensure_companies(pg, [A, B, C])
    article = to_article(synthetic_docs[0])
    load_events(pg, [EventBundle(article, "SEMICONDUCTOR",
                                 [CompanyMention(A.key, 1.0, ("title",)), CompanyMention(B.key, 0.8, ("content",))],
                                 [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None),
                                  Impact(B.key, "REPORTED", 0, (), 0.8, "OK", None)], [], None)], ids)
    return ids, article


def test_publish_writes_snapshot_scores_and_evidence(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert q(pg, "SELECT status, hdfs_uri FROM graph_snapshot") == [("PUBLISHED", "bigkinds://nexus/2026-10-14")]
    assert dict(q(pg, "SELECT window_type, count(*) FROM relationship_score_current GROUP BY 1")) == \
        {"30D": 1, "1Y": 2, "10Y": 2}
    assert q(pg, "SELECT count(*) FROM relationship_score_current WHERE news_score <> score OR disclosure_score IS NOT NULL") == [(0,)]
    mismatched = q(pg, """SELECT rsc.relationship_id FROM relationship_score_current rsc
                          WHERE rsc.evidence_count <> (SELECT count(*) FROM relationship_evidence re
                                                       WHERE re.relationship_id = rsc.relationship_id)""")
    assert mismatched == []
    assert q(pg, "SELECT count(*) FROM source_document WHERE document_id = %s", document_id(MISSING)) == [(1,)]
    assert q(pg, """SELECT cr.relation_source FROM company_relationship cr JOIN relationship_type rt
                    USING (relationship_type_id) WHERE rt.code = 'CO_MENTIONED'""") == [("COMENTION",)]


def test_republish_same_as_of_reuses_snapshot_and_supersedes_others(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF - timedelta(days=7), edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert sorted(q(pg, "SELECT status, count(*) FROM graph_snapshot GROUP BY 1")) == [("PUBLISHED", 1), ("SUPERSEDED", 1)]
    assert q(pg, "SELECT count(*) FROM relationship_score_current") == [(5,)]


def test_reverse_oriented_undirected_relationship_is_reused(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    load_relations(pg, [Edge(B.key, A.key, "PARTNER", 1.0, False, "COSMOS")], [], ids)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert q(pg, """SELECT count(*) FROM company_relationship cr JOIN relationship_type rt USING (relationship_type_id)
                    WHERE rt.code = 'PARTNER'""") == [(1,)]


def test_existing_primary_industry_is_kept(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    pg.execute("INSERT INTO industry (name) VALUES ('기존 업종')")
    pg.execute("INSERT INTO company_industry SELECT %s, industry_id, TRUE FROM industry WHERE name = '기존 업종'", (ids[A.key],))
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    assert sorted(q(pg, """SELECT i.name, ci.is_primary FROM company_industry ci JOIN industry i USING (industry_id)
                           WHERE ci.company_id = %s""", ids[A.key])) == [("기존 업종", True), ("반도체 제조", False)]
    assert q(pg, """SELECT i.name FROM company_industry ci JOIN industry i USING (industry_id)
                    WHERE ci.company_id = %s AND ci.is_primary""", ids[B.key]) == [("반도체 장비",)]


def test_reloading_events_after_publish_does_not_fail(pg, synthetic_docs):
    ids, article = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    load_events(pg, [EventBundle(article, "SEMICONDUCTOR", [CompanyMention(A.key, 1.0, ("title",))],
                                 [Impact(A.key, "REPORTED", 0, (), 1.0, "OK", None)], [], None)], ids)
    assert q(pg, """SELECT count(*) FROM relationship_evidence re JOIN document_evidence de USING (evidence_id)
                    WHERE de.document_id = %s""", document_id(LOADED)) == [(0,)]      # 적재가 지운 문장의 근거는 정리됨


def test_stale_comention_relationships_are_removed(pg, synthetic_docs):
    ids, _ = seed(pg, synthetic_docs)
    publish_galaxy(pg, AS_OF, edges(), ids, ROLES)
    publish_galaxy(pg, AS_OF, edges(with_comention=False), ids, ROLES)
    assert q(pg, "SELECT count(*) FROM company_relationship WHERE relation_source = 'COMENTION'") == [(0,)]
```

`test_reloading_events_after_publish_does_not_fail`에서 근거 문장은 간선 src(A) 기업 행에 붙는다. A의 문서-기업 행은 `load`가 넣은 것이라 `ON CONFLICT DO NOTHING`으로 그 행을 그대로 쓴다. 다시 적재하면 `load`가 그 행을 지우기 전에 관계 근거를 먼저 정리해야 예외가 나지 않는다. 마지막 단언은 적재가 지운 문서-기업 행에 걸려 있던 관계 근거가 함께 정리됐는지 확인한다(다음 `publish-graph`가 다시 채운다).

- [ ] **Step 2: 실패 확인**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_load_galaxy.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.load.galaxy`)

- [ ] **Step 3: V11, `publish_galaxy`, `load_events`·`prune_events` 정리 구현**

- [ ] **Step 4: 통과 확인 (전체 파이썬)**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`
Expected: 전부 통과, skip 0

- [ ] **Step 5: 커밋**

```bash
git add BackEnd/src/main/resources/db/migration/V11__allow_comention_relation_source.sql nexus/nexus/load nexus/tests/test_load_galaxy.py
git commit -m "feat: 빅카인즈 은하 발행 DB 반영 (스냅샷·점수·근거 기사 전체 교체, V11)"
```

---

### Task 4: `publish-graph` 명령

**Files:**
- Modify: `nexus/nexus/pipeline.py`
- Test: `nexus/tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 2 `build_galaxy`, Task 3 `publish_galaxy`, 기존 `_paths`, `_load_master`, `ArticleStore`, `ensure_companies`
- Produces: `galaxy_chains() -> list[str]` — `CHAINS` 중 `analyzed/<chain>/relations.pkl`이 있는 것. CLI `publish-graph --database-url URL`: 각 밸류체인의 사전·기사·`relations.pkl`을 읽어 `build_galaxy`, `ensure_companies`, `publish_galaxy` 순으로 돌리고 `as_of`와 기간별 간선 수(근거 관계·함께 언급 따로), 근거 행 수를 출력한다. 발행할 밸류체인이 없으면 `sys.exit("no analyzed value chain: run analyze first")`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_publish_graph_is_registered():
    with pytest.raises(SystemExit) as exc:
        main(["publish-graph", "--help"])
    assert exc.value.code == 0

def test_publish_graph_needs_an_analyzed_chain(tmp_path, monkeypatch):
    import nexus.pipeline as pipeline
    monkeypatch.setattr(pipeline, "_paths", lambda chain: {"analyzed": tmp_path / chain})
    with pytest.raises(SystemExit) as exc:
        main(["publish-graph", "--database-url", "postgresql://unused"])
    assert "analyze" in str(exc.value.code)
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_pipeline.py -q -k publish_graph`
Expected: FAIL (`invalid choice: 'publish-graph'`)

- [ ] **Step 3: 구현**

- [ ] **Step 4: 통과 확인**

Run: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/pipeline.py nexus/tests/test_pipeline.py
git commit -m "feat: publish-graph 명령 (분석된 모든 밸류체인을 은하 스냅샷 하나로)"
```

---

### Task 5: 백엔드 은하 기본 시장을 KOSPI·코스닥으로

**Files:**
- Modify: `BackEnd/src/main/java/com/cosmos/api/graph/service/GraphService.java`, `docs/api-specification.md` (universe 기본값 `KOSPI100,KOSDAQ`, 허용 값에 `KOSDAQ`)
- Test: `BackEnd/src/test/java/com/cosmos/api/graph/service/GraphServiceIntegrationTest.java`

**Interfaces:**
- Produces: `SUPPORTED_UNIVERSES = Set.of("KOSPI100", "KOSDAQ", "NASDAQ100")`, 기본값 `"KOSPI100,KOSDAQ"`, 매핑 `KOSPI100 → KOSPI`, `KOSDAQ → KOSDAQ`, `NASDAQ100 → NASDAQ`.

- [ ] **Step 1: 테스트 수정과 추가**
  - 기본 universe에 기대던 기존 테스트 세 개(`returnsLatestPublishedGraphForDefaultUniverse`, `filtersLatestGraphByIndustry`, `filtersWholeAndCompanyGraphsByRequestedWindow`)는 `findLatestGraph` 첫 인자를 `"KOSPI100,NASDAQ100"`으로 명시해 기존 기대값을 그대로 유지한다. 첫 테스트 이름은 `returnsLatestPublishedGraphForKospiAndNasdaqUniverse`로 바꾼다.
  - 새 테스트:

```java
@Test
void defaultUniverseShowsKospiAndKosdaqButNotNasdaq() {
    insertCompany(COMPANY_D, "기업D", "D001", "KOSDAQ");

    LatestGraphResponse result = graphService.findLatestGraph(null, null, "30D");

    assertThat(result.nodes()).extracting(node -> node.companyId())
            .containsExactlyInAnyOrder(COMPANY_A, COMPANY_C, COMPANY_D);
    assertThat(result.edges()).isEmpty();          // 시드 간선은 모두 나스닥 기업 B가 걸려 있다
}
```

  (`COMPANY_D`는 테스트 클래스의 다른 UUID 상수와 같은 형식으로 추가한다.)

- [ ] **Step 2: 실패 확인**

Run: 백엔드 테스트 `--tests 'com.cosmos.api.graph.*'`
Expected: `defaultUniverseShowsKospiAndKosdaqButNotNasdaq` FAIL (B가 포함되고 D가 빠짐)

- [ ] **Step 3: 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인 (전체 백엔드)**

Run: 백엔드 전체 테스트
Expected: BUILD SUCCESSFUL, 213개 통과

- [ ] **Step 5: 커밋**

```bash
git add BackEnd/src docs/api-specification.md
git commit -m "feat: 은하 기본 시장을 KOSPI·코스닥으로 (나스닥은 명시할 때만)"
```

---

### Task 6: 은하 화면에 함께 언급 표시

**Files:**
- Create: `FrontEnd/src/lib/edgePick.ts`, `FrontEnd/tests/edgePick.test.ts`
- Modify: `FrontEnd/src/lib/meta.ts`, `FrontEnd/src/lib/graph.ts` (`finish`), `FrontEnd/src/store/galaxy.ts`, `FrontEnd/src/features/galaxy/ControlDock.tsx`, `FrontEnd/src/three/emphasis.ts`, `FrontEnd/src/three/RelationLines.tsx`

**Interfaces:**
- Produces (`edgePick.ts`, import 없는 순수 모듈):
  - `COMENTION = "CO_MENTIONED"`, `COMENTION_DIM = 0.45`, `isComention(type: string): boolean`
  - `interface PairEdge { sourceCompanyId: string; targetCompanyId: string; relationshipType: string; score: number }`
  - `strongestPerPair<T extends PairEdge>(edges: T[]): T[]`: 방향과 관계없는 기업쌍마다 하나. 근거 관계(함께 언급이 아닌 것)가 있으면 그중 점수가 가장 높은 것, 없으면 함께 언급 중 가장 높은 것. 결과는 각 쌍이 입력에 처음 나온 순서.
- `meta.ts`: `RELATIONSHIP_META.CO_MENTIONED` = `{ code: "CO_MENTIONED", label: "함께 언급", color: "#8A93A3", directed: false, description: "같은 문장에 함께 나온 기사 수 기반 · 관계의 근거는 아님" }`. `RELATIONSHIP_ORDER`는 그대로 두고(뉴스 2D 지도·범례·역할이 쓴다), `GALAXY_EDGE_TYPES: RelationshipType[] = [...RELATIONSHIP_ORDER, "CO_MENTIONED"]`를 추가한다.
- `graph.ts` `finish`: 쌍마다 하나 고르는 부분을 `strongestPerPair(edges)`로 바꾼다.
- `store/galaxy.ts`: `activeTypes` 초기값과 초기화 값을 `new Set(GALAXY_EDGE_TYPES)`로. `ControlDock.tsx`: 종류 칩을 `GALAXY_EDGE_TYPES`로.
- `emphasis.ts`: 기본 상태(호버·선택·경로·역할 필터가 없을 때) `CO_MENTIONED` 간선의 강조값에 `COMENTION_DIM`을 곱한다.
- `RelationLines.tsx`: `CO_MENTIONED` 간선은 속도 0, 흐름 구슬 알파 0.

- [ ] **Step 1: 실패하는 테스트 작성** (`FrontEnd/tests/edgePick.test.ts`)

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { strongestPerPair, isComention } from "../src/lib/edgePick.ts";

const e = (s: string, t: string, type: string, score: number) => ({ sourceCompanyId: s, targetCompanyId: t, relationshipType: type, score });

test("a real relation beats a higher-scoring co-mention on the same pair", () => {
  const out = strongestPerPair([e("A", "B", "CO_MENTIONED", 60), e("B", "A", "PARTNER", 61), e("A", "B", "SUPPLY", 70)]);
  assert.deepEqual(out.map((x) => x.relationshipType), ["SUPPLY"]);
  assert.deepEqual(strongestPerPair([e("A", "B", "CO_MENTIONED", 90), e("B", "A", "PARTNER", 61)]).map((x) => x.relationshipType), ["PARTNER"]);
});

test("co-mention stays when it is the only edge, highest score wins", () => {
  const out = strongestPerPair([e("A", "B", "CO_MENTIONED", 20), e("B", "A", "CO_MENTIONED", 40), e("C", "D", "COMPETE", 50)]);
  assert.deepEqual(out.map((x) => [x.relationshipType, x.score]), [["CO_MENTIONED", 40], ["COMPETE", 50]]);
});

test("isComention", () => {
  assert.equal(isComention("CO_MENTIONED"), true);
  assert.equal(isComention("SUPPLY"), false);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd FrontEnd && npm test`
Expected: FAIL (`edgePick.ts`를 찾을 수 없음), 기존 18개 통과

- [ ] **Step 3: 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `cd FrontEnd && npm test && npm run typecheck && npm run lint`
Expected: 21개 통과, 타입·린트 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add FrontEnd/src FrontEnd/tests/edgePick.test.ts
git commit -m "feat: 은하에 함께 언급 간선 표시 (회색·흐림·흐름 없음, 같은 쌍은 근거 관계 우선)"
```

---

### Task 7: 실제 데이터로 다시 돌리고 은하 확인

**Files:**
- Modify: `docs/nexus/slice1-run.md`(§7 추가), `docs/nexus/impact-model-report.md`(명령이 다시 생성)

- [ ] **Step 1: 준비**: `docker start cosmos-postgres cosmos-redis-6380`, 개발 DB에 V11 적용 `nexus/.venv/Scripts/python -X utf8 -m nexus migrate --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos`
Expected: Flyway가 V11을 적용

- [ ] **Step 2: 국내 사전으로 다시 분석** (백그라운드, 로그는 `nexus/data/`)

Run 순서: `build-master --chain semis` → `analyze --chain semis` → `load --chain semis --database-url …` → `train-impact --chain semis` → `predict-impact --chain semis --database-url …`
Expected: `build-master`가 `companies: 61`, 미해결 0. `analyze`가 이벤트 수와 추정 수를 출력. `load`가 사라진 이벤트를 정리. 학습·예측이 끝까지 돈다(지표가 나빠도 기록하고 진행)

- [ ] **Step 3: 은하 발행**

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus publish-graph --database-url postgresql://cosmos:cosmos1234@localhost:5432/cosmos`
Expected: `as_of 2026-10-14`, 기간별 간선 수(근거 관계·함께 언급), 근거 행 수. 확인 SQL: 발행 스냅샷 1개, `relationship_score_current`의 기간별 행 수, 나스닥 기업이 걸린 점수 행 0개, `event_company_impact`에 나스닥 기업 행 0개

- [ ] **Step 4: 화면 확인**

백엔드(`REDIS_PORT=6380 SERVER_PORT=18081`)와 프론트를 띄운다. 확인 항목:
- `/`에서 국내 기업 행성과 간선이 보이는지
- 30일·1년·전체 탭
- 함께 언급 토글
- 간선을 눌렀을 때 근거 기사
- `/events`에 나스닥 노드가 없는지
- 콘솔 오류 없음

창이 가려져 스크린샷이 안 되면 DOM과 API로 확인한다.

- [ ] **Step 5: 기록·정리·커밋**

`slice1-run.md`에 `## 7. 국내 상장사 재분석과 은하 발행 (날짜)`을 추가한다. 내용은 이벤트·추정·영향 모델 지표의 전후 비교, 기간별 간선 수, 소요 시간이다. 띄운 서버와 컨테이너를 모두 닫는다.

```bash
git add docs/nexus/slice1-run.md docs/nexus/impact-model-report.md
git commit -m "docs: 국내 상장사 재분석과 빅카인즈 은하 발행 결과"
```
