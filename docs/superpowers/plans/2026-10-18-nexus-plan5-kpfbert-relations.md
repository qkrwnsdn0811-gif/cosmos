# NEXUS 계획 5: KPF-BERT 관계 판별기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 캐시된 빅카인즈 hilight 문장을 KPF-BERT로 판별해, 정확도 0.85 이상을 지키면서 근거 관계를 늘린다.

**Architecture:** `nexus/nexus/relations/`에 문장 모으기(fragments)·라벨 도구(labels)·판별기(kpfbert) 모듈을 추가한다. 라벨링은 Claude 하위 에이전트가 맡는다. `build_graph`는 판별기가 있으면 같은 검색 결과에서 모은 문장을 판별해 근거 조각을 더하고, 없으면 지금처럼 규칙만 쓴다.

**Tech Stack:** Python 3.12, pandas, torch 2.14.1(CPU), transformers 5.16.1, pytest

**Spec:** `docs/superpowers/specs/2026-10-18-nexus-kpfbert-relation-design.md`

## Global Constraints

- 파이썬 명령은 저장소 루트에서 `nexus/.venv/Scripts/python -X utf8 ...`. 전체 테스트: `TEST_DATABASE_URL=postgresql://cosmos:cosmos1234@localhost:5432/postgres nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests -q`. torch가 필요한 테스트는 `pytest.importorskip("torch")`.
- torch·transformers는 `kpfbert.py`의 함수 안에서만 import한다.
- 스펙 고정값:
  - 모델 `jinmang2/kpfbert`, 리비전 `dc732cc2aaa998959a2721e06ed10f98f2c51a0f`, safetensors만 사용, `trust_remote_code=False`. 최대 길이 128.
  - 라벨 `LABELS = ("SUPPLY_AB", "SUPPLY_BA", "INVEST_AB", "INVEST_BA", "PARTNER", "COMPETE", "NONE")`. A·B는 기업 키 사전순(A < B).
  - 표시: A는 `@ 이름 @`, B는 `# 이름 #`.
  - 표본 2,000개, 규칙 판정 문장 전부 포함, 무작위 채움은 기업쌍당 20개 상한, `random_state` 0.
  - 분할: 기업쌍 단위 70 / 15 / 15.
  - 학습: 학습률 2e-5, 배치 16, 4에폭, 클래스 가중치(빈도 역수), CPU 8스레드, 시드 0.
  - τ 후보 0.50~0.95(0.05 간격): 검증 구간에서 NONE이 아니고 최대 확률 ≥ τ인 판별의 정확도 ≥ 0.85인 가장 작은 값, 없으면 0.95.
  - 판별 근거 조각: `pattern = "kpfbert"`, `confidence` = 최대 확률(소수 넷째 자리).
  - 파일: `nexus/data/relations/` 아래 `fragments.parquet`, `sample.jsonl`, `labels.jsonl`, `review.csv`, `model/`. 보고서 `docs/nexus/relation-model-report.md`.
- 뉴스 문장·라벨·모델은 레포에 커밋하지 않는다(`nexus/data/`는 git 제외).
- 커밋은 작업마다 로컬에만, Claude 표기 금지, push 금지. 작업이 끝나면 띄운 서버와 컨테이너를 닫고 알림음을 울린다.

## Review Focus

1. **라벨 파일에 빠진 문장·잘못된 라벨 값이 섞임** → 읽을 때 걸러내고 몇 개를 걸렀는지 알려야 한다. (Task 2 테스트)
2. **한 문장에 같은 회사 이름이 두 번 나옴** → 표시는 단독 이름으로 처음 나온 곳에만 붙고, 다른 단어 속 이름(예: "테스트" 안의 "테스")에는 붙지 않아야 한다. (Task 2 테스트)
3. **검증 구간에 관계 판별이 하나도 없음** → τ는 0.95로 떨어지고 학습은 끝까지 가야 한다. (Task 2 테스트)
4. **모델 폴더는 있는데 torch가 없음** → analyze는 규칙만으로 돌아야 한다. (Task 3 테스트)
5. **판별과 규칙이 같은 문장을 같은 관계로 잡음** → 근거 조각이 두 번 세어지면 안 된다. (Task 4 테스트)

---

## 파일 구조

```text
nexus/nexus/bigkinds/client.py      CacheOnlyClient (캐시만 읽고 네트워크를 부르지 않음)
nexus/nexus/relations/evidence.py   rule_hit(fragment, x, y): 규칙 판정 원본(느슨한 패턴 포함)
nexus/nexus/relations/fragments.py  Fragment, fragment_id, pair_fragments, collect_fragments, 입출력, 표본
nexus/nexus/relations/labels.py     LABELS, marked_text, flip_label, 라벨 입출력, review.csv, split_by_pair, choose_threshold
nexus/nexus/relations/kpfbert.py    model_snippets(순수), load_predictor, train_relation_model, render_relation_report
nexus/nexus/pipeline.py             relation-fragments / train-relations 명령, build_graph(predictor=)
nexus/tests/test_fragments.py, test_labels.py, test_kpfbert.py (+ test_pipeline.py 추가분)
```

---

### Task 1: 캐시 전용 클라이언트와 문장 모으기

**Files:**
- Modify: `nexus/nexus/bigkinds/client.py`, `nexus/nexus/relations/evidence.py`
- Create: `nexus/nexus/relations/fragments.py`
- Test: `nexus/tests/test_fragments.py`

**Interfaces:**
- Produces:
  - `CacheOnlyClient(client: BigKindsClient)`: `post(path, argument, *, cache) -> dict`는 캐시 파일이 있으면 그 `return_object`, 없으면 `{"documents": []}`를 돌려주고 `misses`를 1 늘린다. 네트워크를 부르지 않는다.
  - `evidence.rule_hit(fragment: str, x: str, y: str) -> tuple[str, str, str, float, str] | None`: 지금 `extract_evidence`가 쓰는 판정(COSMOS 두 방향 → 지분 두 방향)을 그대로 돌려준다. 느슨한 협력 패턴도 걸러내지 않는다. `extract_evidence`는 이 함수를 쓰도록 바꾼다(동작 변화 없음).
  - `@dataclass(frozen=True) Fragment(fragment_id: str, a_key: str, b_key: str, a_alias: str, b_alias: str, text: str, news_id: str, title: str, provider: str, published: datetime, link: str, rule_rel: str | None, rule_pattern: str | None)`
  - `fragment_id(text: str, a_key: str, b_key: str) -> str`: `sha1(f"{a_key}|{b_key}|{text}")`의 16진수 앞 16자
  - `pair_fragments(docs: list[dict], a: Company, b: Company, aliases_of) -> list[Fragment]`: A·B를 키 순으로 맞춘 뒤, `hilight_fragments`로 나눈 문장 중 두 회사가 모두 단독 이름으로 나오는 문장. `rule_hit` 결과는 A·B 기준으로 기록한다(`rule_rel`은 `SUPPLY_AB` 같은 라벨 형식, 협력·경쟁은 그대로, 없으면 None).
  - `collect_fragments(articles, recognizer, companies, client, *, start, end, today, top_pairs: int = 300) -> list[Fragment]`: `build_graph`와 같은 검색(상위 문장 동시언급 쌍 기본 검색 + `candidate_pairs` 관계 단어 검색). `fragment_id`로 중복 제거.
  - `write_fragments(fragments, path)`, `read_fragments(path) -> list[Fragment]` (parquet)
  - `sample_for_labeling(fragments, size: int = 2000, per_pair: int = 20, seed: int = 0) -> list[Fragment]`: `rule_rel`이 있는 문장은 모두 넣고, 나머지는 시드로 섞은 순서대로 넣되 그 기업쌍이 표본에 `per_pair`개 이상이면 건너뛴다.

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_fragments.py`)

```python
import json
from datetime import date

import pytest

from nexus.bigkinds.client import BigKindsClient
from nexus.models import Company
from nexus.relations.fragments import (Fragment, fragment_id, pair_fragments, read_fragments, sample_for_labeling,
                                       write_fragments)
from nexus.bigkinds.client import CacheOnlyClient

A = Company("KOSPI:009150", "KOSPI", "009150", "삼성전기", "SEMICONDUCTOR", "부품")
B = Company("KOSDAQ:357780", "KOSDAQ", "357780", "솔브레인", "SEMICONDUCTOR", "소재")
T = Company("KOSDAQ:095610", "KOSDAQ", "095610", "테스", "SEMICONDUCTOR", "장비")
aliases = lambda key: {A.key: ["삼성전기"], B.key: ["솔브레인"], T.key: ["테스"]}[key]


def doc(hilight, news_id="N1"):
    return {"news_id": news_id, "title": "제목", "hilight": hilight, "dateline": "2026-07-01T10:00:00.000+09:00",
            "provider": "합성일보", "provider_link_page": ""}


def test_cache_only_client_never_calls_the_network(tmp_path):
    real = BigKindsClient("key", tmp_path, transport=lambda path, body: pytest.fail("network call"))
    client = CacheOnlyClient(real)
    assert client.post("/search/news", {"query": "x"}, cache=True) == {"documents": []} and client.misses == 1
    real._cache_file("/search/news", {"query": "y"}).parent.mkdir(parents=True, exist_ok=True)
    real._cache_file("/search/news", {"query": "y"}).write_text(json.dumps({"return_object": {"documents": [1]}}),
                                                                  encoding="utf-8")
    assert client.post("/search/news", {"query": "y"}, cache=True) == {"documents": [1]}


def test_pair_fragments_orders_pair_and_keeps_rule_judgement():
    frags = pair_fragments([doc("<b>삼성전기</b>는 국내 재료 회사인 <b>솔브레인</b>과 협력해 연구에 착수했다.")], B, A, aliases)
    [f] = frags
    assert (f.a_key, f.b_key) == tuple(sorted((A.key, B.key)))
    assert f.rule_rel == "PARTNER" and f.rule_pattern == "clause+kw"
    assert f.fragment_id == fragment_id(f.text, f.a_key, f.b_key) and len(f.fragment_id) == 16


def test_pair_fragments_records_direction_in_ab_terms():
    [f] = pair_fragments([doc("<b>삼성전기</b>는 <b>솔브레인</b>에 장비를 납품한다.")], A, B, aliases)
    expected = "SUPPLY_AB" if f.a_key == A.key else "SUPPLY_BA"
    assert f.rule_rel == expected


def test_pair_fragments_skips_names_inside_other_words():
    assert pair_fragments([doc("<b>삼성전기</b>는 신규 <b>테스</b>트를 마쳤다.")], A, T, aliases) == []


def test_fragments_roundtrip(tmp_path):
    [f] = pair_fragments([doc("<b>삼성전기</b>는 <b>솔브레인</b>에 장비를 납품한다.")], A, B, aliases)
    write_fragments([f], tmp_path / "f.parquet")
    assert read_fragments(tmp_path / "f.parquet") == [f]


def make(i, pair, rule=None):
    a, b = pair
    return Fragment(f"{i:016x}", a, b, "가", "나", f"문장 {i}", f"N{i}", "", "", None, "", rule, rule and "p")


def test_sample_keeps_rule_hits_and_caps_each_pair():
    frags = [make(i, ("A", "B")) for i in range(50)] + [make(100 + i, ("A", "C")) for i in range(5)]
    frags += [make(200, ("A", "B"), rule="PARTNER")]
    sample = sample_for_labeling(frags, size=30, per_pair=20)
    assert any(f.rule_rel for f in sample)
    assert sum(1 for f in sample if (f.a_key, f.b_key) == ("A", "B")) <= 20
    assert len({f.fragment_id for f in sample}) == len(sample) == 25          # 20 (상한) + 5
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_fragments.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.relations.fragments`)

- [ ] **Step 3: 구현** (위 Interfaces 그대로. `Fragment.published`는 hilight 문서의 `dateline`을 `datetime.fromisoformat`으로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_fragments.py nexus/tests/test_relations.py nexus/tests/test_invest.py -q`
Expected: 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/bigkinds/client.py nexus/nexus/relations nexus/tests/test_fragments.py
git commit -m "feat: 관계 판별용 문장 모으기 (캐시 전용, 기업쌍 순서·규칙 판정 기록, 라벨링 표본)"
```

---

### Task 2: 라벨 도구 (표시·뒤집기·입출력·분할·확신 기준)

**Files:**
- Create: `nexus/nexus/relations/labels.py`
- Test: `nexus/tests/test_labels.py`

**Interfaces:**
- Consumes: Task 1 `Fragment`, `evidence._standalone`
- Produces:
  - `LABELS` (Global Constraints 값), `RELATION_LABELS = LABELS[:-1]`
  - `marked_text(f: Fragment, swap: bool = False) -> str`: A 별칭의 첫 단독 출현을 `@ … @`, B 별칭의 첫 단독 출현을 `# … #`로 감싼다(`swap`이면 A에 `#`, B에 `@`)
  - `flip_label(label: str) -> str`: `_AB` ↔ `_BA`, 나머지는 그대로
  - `write_sample(fragments, path, names: dict[str, str])`: JSONL 한 줄에 `{"fragment_id", "a", "b", "text"}` (a·b는 기업 이름)
  - `read_labels(path, known_ids: set[str]) -> tuple[dict[str, str], int]`: `{"fragment_id": …, "label": …}` 줄을 읽어 라벨 사전과 걸러낸 줄 수(모르는 id, LABELS 밖 값, 깨진 JSON)를 돌려준다
  - `write_review(labels: dict[str, str], fragments: dict[str, Fragment], path, names, n: int = 100, seed: int = 0)`: CSV(UTF-8 BOM) 열 `fragment_id, a, b, text, label, ok`(ok는 빈칸)
  - `split_by_pair(ids_by_pair: dict[tuple[str, str], list[str]], seed: int = 0) -> tuple[list[str], list[str], list[str]]`: 기업쌍을 시드로 섞어 쌍 단위로 70/15/15에 배정(문장 수 기준 누적 비율로 자름)
  - `choose_threshold(predictions: list[tuple[str, float]], gold: list[str]) -> float`

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_labels.py`)

```python
import json

from nexus.relations.fragments import Fragment
from nexus.relations.labels import (LABELS, choose_threshold, flip_label, marked_text, read_labels, split_by_pair,
                                    write_review, write_sample)


def frag(text, a="삼성전기", b="솔브레인", fid="0" * 16):
    return Fragment(fid, "KOSPI:009150", "KOSDAQ:357780", a, b, text, "N1", "", "", None, "", None, None)


def test_marks_first_standalone_mentions():
    f = frag("삼성전기는 솔브레인과 협력했고 삼성전기 측은 만족했다.")
    assert marked_text(f) == "@ 삼성전기 @는 # 솔브레인 #과 협력했고 삼성전기 측은 만족했다."
    assert marked_text(f, swap=True) == "# 삼성전기 #는 @ 솔브레인 @과 협력했고 삼성전기 측은 만족했다."


def test_marks_skip_names_inside_other_words():
    f = frag("신규 테스트를 마친 테스는 디아이와 경쟁한다.", a="테스", b="디아이")
    assert marked_text(f) == "신규 테스트를 마친 @ 테스 @는 # 디아이 #와 경쟁한다."


def test_flip_label():
    assert [flip_label(x) for x in LABELS] == ["SUPPLY_BA", "SUPPLY_AB", "INVEST_BA", "INVEST_AB", "PARTNER", "COMPETE", "NONE"]


def test_read_labels_filters_bad_lines(tmp_path):
    p = tmp_path / "labels.jsonl"
    p.write_text("\n".join([json.dumps({"fragment_id": "a", "label": "PARTNER"}),
                            json.dumps({"fragment_id": "b", "label": "FRIEND"}),
                            json.dumps({"fragment_id": "zzz", "label": "NONE"}),
                            "{broken"]), encoding="utf-8")
    labels, dropped = read_labels(p, {"a", "b"})
    assert labels == {"a": "PARTNER"} and dropped == 3


def test_sample_and_review_files(tmp_path):
    f = frag("삼성전기는 솔브레인과 협력했다.")
    names = {"KOSPI:009150": "삼성전기", "KOSDAQ:357780": "솔브레인"}
    write_sample([f], tmp_path / "s.jsonl", names)
    assert json.loads((tmp_path / "s.jsonl").read_text(encoding="utf-8")) == \
        {"fragment_id": f.fragment_id, "a": "삼성전기", "b": "솔브레인", "text": f.text}
    write_review({f.fragment_id: "PARTNER"}, {f.fragment_id: f}, tmp_path / "r.csv", names, n=100)
    assert (tmp_path / "r.csv").read_text(encoding="utf-8-sig").splitlines()[0] == "fragment_id,a,b,text,label,ok"


def test_split_keeps_each_pair_in_one_part():
    ids = {(f"A{i}", f"B{i}"): [f"{i}-{j}" for j in range(i % 5 + 1)] for i in range(40)}
    train, valid, test = split_by_pair(ids)
    parts = [set(train), set(valid), set(test)]
    for pair_ids in ids.values():
        assert sum(1 for part in parts if set(pair_ids) & part) == 1
    assert len(train) > len(valid) > 0 and len(test) > 0


def test_choose_threshold():
    preds = [("PARTNER", 0.55)] * 10 + [("PARTNER", 0.9)] * 10 + [("NONE", 0.99)] * 5
    gold = ["NONE"] * 10 + ["PARTNER"] * 10 + ["NONE"] * 5
    assert choose_threshold(preds, gold) == 0.6                       # 0.55짜리 오답이 빠지는 첫 값
    assert choose_threshold([("NONE", 0.9)] * 5, ["PARTNER"] * 5) == 0.95   # 관계 판별이 하나도 없음
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_labels.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.relations.labels`)

- [ ] **Step 3: 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_labels.py -q`
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/relations/labels.py nexus/tests/test_labels.py
git commit -m "feat: 관계 라벨 도구 (이름 표시, 방향 뒤집기, 라벨 입출력, 기업쌍 분할, 확신 기준)"
```

---

### Task 3: KPF-BERT 판별기 (학습·판별·근거 조각·보고서)

**Files:**
- Create: `nexus/nexus/relations/kpfbert.py`
- Test: `nexus/tests/test_kpfbert.py`

**Interfaces:**
- Consumes: Task 1 `Fragment`, Task 2 `LABELS`, `marked_text`, `flip_label`, `choose_threshold`
- Produces:
  - `MODEL_ID`, `REVISION`, `MAX_LENGTH = 128`
  - `Predictor = Callable[[list[Fragment]], list[tuple[str, float]]]`
  - `model_snippets(fragments: list[Fragment], predictions: list[tuple[str, float]], tau: float) -> list[EvidenceSnippet]`: NONE이 아니고 확률 ≥ τ인 것만. `SUPPLY_AB`·`INVEST_AB`는 src=A, dst=B, `_BA`는 반대, 협력·경쟁은 src=A, dst=B. `rel`은 `SUPPLY|INVEST|PARTNER|COMPETE`, `pattern="kpfbert"`, `confidence=round(prob, 4)`, 나머지 필드는 Fragment에서.
  - `load_predictor(model_dir: Path, threads: int = 8) -> tuple[Predictor, float] | None`: 폴더(`config.json`, `tau.json`)가 없거나 torch·transformers를 import할 수 없으면 None.
  - `train_relation_model(train, valid, test, out_dir, *, base: str = MODEL_ID, revision: str | None = REVISION, epochs: int = 4, lr: float = 2e-5, batch: int = 16, threads: int = 8, seed: int = 0) -> dict`: `train`·`valid`·`test`는 `list[tuple[Fragment, str]]`. 학습 데이터는 맞바꾼 사본을 더한다. 검증 구간으로 τ를 고르고, 모델·토크나이저·`tau.json`(τ, 라벨 목록, 리비전)을 저장하고, 시험 구간 지표가 든 사전을 돌려준다(`tau`, `test_precision`, `test_recall`, 클래스별 수치, 혼동 행렬).
  - `render_relation_report(stats: dict) -> str`: 스펙 §8 항목의 마크다운.

- [ ] **Step 1: 실패하는 테스트 작성** (`nexus/tests/test_kpfbert.py`)

```python
from datetime import datetime, timedelta, timezone

import pytest

from nexus.relations.fragments import Fragment
from nexus.relations.kpfbert import load_predictor, model_snippets, render_relation_report

KST = timezone(timedelta(hours=9))


def frag(i):
    return Fragment(f"{i:016x}", "KOSPI:A", "KOSDAQ:B", "가전자", "나소재", f"가전자는 나소재와 문장 {i}", f"N{i}", "제목",
                    "합성일보", datetime(2026, 7, 1, 10, tzinfo=KST), "", None, None)


def test_model_snippets_follow_label_direction_and_threshold():
    frags = [frag(1), frag(2), frag(3), frag(4)]
    preds = [("SUPPLY_BA", 0.91), ("PARTNER", 0.88), ("NONE", 0.99), ("COMPETE", 0.5)]
    out = model_snippets(frags, preds, tau=0.8)
    assert [(s.src, s.dst, s.rel, s.pattern, s.confidence) for s in out] == [
        ("KOSDAQ:B", "KOSPI:A", "SUPPLY", "kpfbert", 0.91), ("KOSPI:A", "KOSDAQ:B", "PARTNER", "kpfbert", 0.88)]
    assert out[0].news_id == "N1" and out[0].snippet == frags[0].text


def test_load_predictor_without_model_folder(tmp_path):
    assert load_predictor(tmp_path / "missing") is None


def test_load_predictor_without_torch(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tau.json").write_text('{"tau": 0.8}', encoding="utf-8")
    import builtins
    real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.startswith("torch")
                        else real_import(name, *a, **k))
    assert load_predictor(tmp_path) is None


def test_report_has_sections():
    text = render_relation_report({"labels": {"PARTNER": 10, "NONE": 90}, "review": None, "tau": 0.8,
                                   "test_precision": 0.9, "test_recall": 0.5, "rule_precision": 0.95,
                                   "rule_recall": 0.2, "per_class": {}, "confusion": [], "relations_before": 37,
                                   "relations_after": 60, "companies_after": 35})
    for head in ("# NEXUS 관계 판별기 검증 보고서", "## 시험 구간", "사람 검토 대기", "37 → 60"):
        assert head in text


def test_training_runs_on_a_tiny_model(tmp_path):
    pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from nexus.relations.kpfbert import train_relation_model
    base = tmp_path / "tiny"
    vocab = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]", "@", "#", "가전자", "나소재", "협력", "공급", "문장"]
    base.mkdir()
    (base / "vocab.txt").write_text("\n".join(vocab), encoding="utf-8")
    transformers.BertTokenizer(str(base / "vocab.txt")).save_pretrained(base)
    transformers.BertForSequenceClassification(transformers.BertConfig(
        vocab_size=len(vocab), hidden_size=16, num_hidden_layers=1, num_attention_heads=2, intermediate_size=32,
        num_labels=7)).save_pretrained(base)
    data = [(frag(i), "PARTNER" if i % 2 else "NONE") for i in range(16)]
    stats = train_relation_model(data[:10], data[10:13], data[13:], tmp_path / "out", base=str(base), revision=None,
                                 epochs=1, batch=4, threads=1)
    assert 0.5 <= stats["tau"] <= 0.95 and (tmp_path / "out" / "tau.json").is_file()
    predictor, tau = load_predictor(tmp_path / "out", threads=1)
    assert len(predictor([frag(1), frag(2)])) == 2
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_kpfbert.py -q`
Expected: FAIL (`ModuleNotFoundError: nexus.relations.kpfbert`)

- [ ] **Step 3: 구현** (위 Interfaces 그대로. 학습 루프는 torch로 직접 작성하고 `torch.manual_seed(seed)`·`torch.set_num_threads(threads)`. 판별은 배치 32, `softmax` 최대값)

- [ ] **Step 4: 통과 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_kpfbert.py -q`
Expected: 5 passed (torch가 있으면 작은 모델 학습 포함)

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/relations/kpfbert.py nexus/tests/test_kpfbert.py
git commit -m "feat: KPF-BERT 관계 판별기 학습·판별과 근거 조각 변환, 검증 보고서"
```

---

### Task 4: 명령과 analyze 연결

**Files:**
- Modify: `nexus/nexus/pipeline.py`
- Test: `nexus/tests/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1~3 전부
- Produces:
  - `build_graph(..., predictor: tuple[Predictor, float] | None = None)`: 검색 결과마다 `extract_evidence`(규칙)와 `pair_fragments`를 함께 모은다. predictor가 있으면 모은 문장을 한 번에 판별해 `model_snippets`를 더한 뒤 `_unique_snippets`로 합친다. 판별과 규칙이 같은 (기사, 문장, 관계)를 내면 한 번만 남기되 규칙 쪽을 남긴다. 무방향 관계(협력·경쟁)는 (src, dst)를 정렬한 키로 비교한다(규칙은 문장 속 순서, 판별은 A·B 순서로 기록하기 때문).
  - `cmd_analyze`는 `load_predictor(p["data"] / "relations" / "model")` 결과를 넘기고, 판별기 사용 여부를 출력한다.
  - CLI `relation-fragments --chain semis`: `CacheOnlyClient`로 `collect_fragments` → `fragments.parquet`, `sample_for_labeling` → `sample.jsonl`. 문장 수·표본 수·캐시 미스 수를 출력한다.
  - CLI `train-relations --chain semis`: `read_labels` → 기업쌍 분할 → `train_relation_model` → 보고서 `docs/nexus/relation-model-report.md`. 라벨이 없으면 `sys.exit("no labels: label nexus/data/relations/sample.jsonl first")`.

- [ ] **Step 1: 실패하는 테스트 작성** (`test_pipeline.py`에 추가)

```python
def test_build_graph_adds_model_snippets_without_double_counting(tmp_path):
    from datetime import datetime, timedelta, timezone

    from nexus.models import Article
    from nexus.pipeline import build_graph

    a = Company("KOSPI:009150", "KOSPI", "009150", "삼성전기", "SEMICONDUCTOR", "부품")
    b = Company("KOSDAQ:357780", "KOSDAQ", "357780", "솔브레인", "SEMICONDUCTOR", "소재")
    recognizer = Recognizer([a, b], alias_rows([a, b], [], []))
    kst = timezone(timedelta(hours=9))
    article = Article("n1", "t", "", date(2026, 10, 13), datetime(2026, 10, 13, 10, tzinfo=kst), "p", "", (), (), "",
                      ("삼성전기", "솔브레인"), (), ())
    rule_text = "이를 위해 삼성전기는 국내 재료 회사인 솔브레인과 협력해 연구에 착수했다."
    model_text = "삼성전기와 솔브레인이 유리기판 소재를 함께 만든다."
    hits = [{"news_id": f"x{i}", "title": "제목", "dateline": "2026-07-01T10:00:00.000+09:00", "provider": "p",
             "provider_link_page": "", "hilight": t} for i, t in enumerate((rule_text, model_text))]

    class Client:
        def post(self, path, argument, *, cache):
            return {"documents": hits}

    def predictor(frags):
        return [("PARTNER", 0.97) for _ in frags]                       # 규칙 문장도 같은 관계로 판별

    cosmos_csv = tmp_path / "rel.csv"
    cosmos_csv.write_text("source_market,source_stock_code,target_market,target_stock_code,relationship_type_code" + chr(10),
                          encoding="utf-8")
    _, edges, snippets = build_graph([article], recognizer, {c.key: c for c in (a, b)}, Client(),
                                     start=date(2025, 1, 1), end=date(2026, 10, 14), today=date(2026, 10, 14),
                                     cosmos_csv=cosmos_csv, predictor=(predictor, 0.9))
    assert sorted((s.snippet, s.pattern) for s in snippets) == [(model_text, "kpfbert"), (rule_text, "clause+kw")]


@pytest.mark.parametrize("command", ["relation-fragments", "train-relations"])
def test_relation_commands_are_registered(command):
    with pytest.raises(SystemExit) as exc:
        main([command, "--help"])
    assert exc.value.code == 0
```

- [ ] **Step 2: 실패 확인**

Run: `nexus/.venv/Scripts/python -X utf8 -m pytest nexus/tests/test_pipeline.py -q`
Expected: FAIL (`unexpected keyword argument 'predictor'`, `invalid choice`)

- [ ] **Step 3: 구현** (위 Interfaces 그대로)

- [ ] **Step 4: 통과 확인 (전체 파이썬)**

Run: 전체 파이썬 테스트
Expected: 전부 통과, skip 0(torch 설치 환경)

- [ ] **Step 5: 커밋**

```bash
git add nexus/nexus/pipeline.py nexus/tests/test_pipeline.py
git commit -m "feat: relation-fragments·train-relations 명령과 analyze 판별기 연결"
```

---

### Task 5: 실제 실행 — 문장 모으기, 라벨링, 학습, 반영

**Files:**
- Create (명령이 생성): `docs/nexus/relation-model-report.md`
- Modify: `docs/nexus/slice1-run.md`(§9 추가)

- [ ] **Step 1: 문장 모으기**

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus relation-fragments --chain semis`
Expected: 문장 약 10,332개, 표본 약 2,000개, 캐시 미스 0

- [ ] **Step 2: Claude 라벨링**

`sample.jsonl`을 500줄씩 4개 파일로 나눈다. 일반 하위 에이전트 4개를 병렬로 띄우고, 각자 자기 파일을 읽어 `labels-<n>.jsonl`을 쓰게 한다.
- 지시문에 넣을 것: 7개 라벨 정의, "두 회사의 관계를 문장이 직접 말할 때만" 기준, 맞는 예와 틀린 예(제3자 나열, 법 이름 속 '경쟁', 기대·계획 표현), 출력 형식 `{"fragment_id": "...", "label": "..."}`, 모든 줄에 하나씩.
- 합친 뒤 `read_labels`로 확인한다. 걸러진 줄이나 빠진 id가 있으면 그 줄만 다시 요청한다.
- `write_review`로 `review.csv`(100행)를 만들어 사용자에게 알린다.

Expected: `labels.jsonl` 약 2,000줄, 걸러진 줄 0

- [ ] **Step 3: 학습** (KPF-BERT 가중치 454MB 첫 다운로드 — 사용자가 2026-10-18 A안과 함께 승인)

Run: `nexus/.venv/Scripts/python -X utf8 -m nexus train-relations --chain semis` (백그라운드, 로그 `nexus/data/train-relations.log`)
Expected: τ와 시험 정확도·재현율 출력, 보고서 생성. 시험 정확도가 0.85 미만이면 모델 폴더를 지우고 규칙만 유지한다고 보고서에 기록한다(스펙 §10).

- [ ] **Step 4: 반영**

DB를 켜고 `analyze → load → train-impact → predict-impact → publish-graph`를 다시 돌린다(검색은 캐시).
Expected: analyze가 "relation model: on"을 출력하고 근거 관계 수가 37보다 늘어난다. 은하 기간별 근거 관계 수를 확인한다.

- [ ] **Step 5: 기록·정리·커밋**

보고서에 판별 적용 후 관계 수를 채우고, `slice1-run.md`에 `## 9. KPF-BERT 관계 판별기 (날짜)`를 추가한다. DB를 닫고 알림음을 울린다.

```bash
git add docs/nexus/relation-model-report.md docs/nexus/slice1-run.md
git commit -m "docs: KPF-BERT 관계 판별기 결과"
```
