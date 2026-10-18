"""KPF-BERT relation classifier: fine-tune on labelled pair fragments, predict, and turn predictions into evidence.

The input marks the two companies in the sentence (A as '@ … @', B as '# … #'); the output is one of LABELS.
torch/transformers are imported inside the functions so the pipeline still runs (rules only) without them.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Callable

from ..device import padding_for, pick_device
from ..models import EvidenceSnippet
from .evidence import LOOSE_PARTNER_PATTERNS
from .fragments import Fragment
from .labels import LABELS, RELATION_LABELS, choose_threshold, flip_label, marked_text

MODEL_ID = "jinmang2/kpfbert"
REVISION = "dc732cc2aaa998959a2721e06ed10f98f2c51a0f"
MAX_LENGTH = 128
PREDICT_BATCH = 32
EPS = 1e-9
REL_OF = {"SUPPLY_AB": ("SUPPLY", False), "SUPPLY_BA": ("SUPPLY", True), "INVEST_AB": ("INVEST", False),
          "INVEST_BA": ("INVEST", True), "PARTNER": ("PARTNER", False), "COMPETE": ("COMPETE", False)}

Predictor = Callable[[list[Fragment]], list[tuple[str, float]]]


def model_snippets(fragments: list[Fragment], predictions: list[tuple[str, float]], tau: float) -> list[EvidenceSnippet]:
    out = []
    for f, (label, prob) in zip(fragments, predictions):
        if label not in REL_OF or prob < tau - EPS:
            continue
        rel, reverse = REL_OF[label]
        src, dst = (f.b_key, f.a_key) if reverse else (f.a_key, f.b_key)
        out.append(EvidenceSnippet(news_id=f.news_id, src=src, dst=dst, rel=rel, snippet=f.text[:300], pattern="kpfbert",
                                   confidence=round(prob, 4), title=f.title, provider=f.provider, published=f.published,
                                   link=f.link))
    return out


def _predict(model, tokenizer, texts: list[str], labels: tuple[str, ...]) -> list[tuple[str, float]]:
    import torch
    device = next(model.parameters()).device
    pad = padding_for(device.type, MAX_LENGTH)
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), PREDICT_BATCH):
            enc = tokenizer(texts[i:i + PREDICT_BATCH], return_tensors="pt", **pad).to(device)
            best, idx = torch.softmax(model(**enc).logits, dim=-1).max(dim=-1)
            out += [(labels[j], float(p)) for j, p in zip(idx.tolist(), best.tolist())]
    return out


def load_predictor(model_dir: Path, threads: int = 8, device: str | None = None) -> tuple[Predictor, float] | None:
    """(predictor, τ) from a trained model folder; None when the folder or torch/transformers is missing."""
    model_dir = Path(model_dir)
    if not (model_dir / "config.json").is_file() or not (model_dir / "tau.json").is_file():
        return None
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError:
        return None
    meta = json.loads((model_dir / "tau.json").read_text(encoding="utf-8"))
    labels = tuple(meta.get("labels", LABELS))
    torch.set_num_threads(threads)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir).to(device or pick_device(torch)).eval()

    def predict(fragments: list[Fragment]) -> list[tuple[str, float]]:
        return _predict(model, tokenizer, [marked_text(f) for f in fragments], labels)

    return predict, float(meta["tau"])


def _accepted_scores(predictions: list[tuple[str, float]], gold: list[str], tau: float) -> tuple[float | None, float | None]:
    accepted = [(label, g) for (label, prob), g in zip(predictions, gold) if label != "NONE" and prob >= tau - EPS]
    correct = sum(label == g for label, g in accepted)
    relations = sum(g != "NONE" for g in gold)
    return (correct / len(accepted) if accepted else None, correct / relations if relations else None)


def _rule_label(f: Fragment) -> str:
    if not f.rule_rel or (f.rule_rel == "PARTNER" and f.rule_pattern in LOOSE_PARTNER_PATTERNS):
        return "NONE"
    return f.rule_rel


def train_relation_model(train: list[tuple[Fragment, str]], valid: list[tuple[Fragment, str]],
                         test: list[tuple[Fragment, str]], out_dir: Path, *, base: str = MODEL_ID,
                         revision: str | None = REVISION, epochs: int = 4, lr: float = 2e-5, batch: int = 16,
                         threads: int = 8, seed: int = 0, device: str | None = None) -> dict:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    pinned = {"revision": revision} if revision else {}
    tokenizer = AutoTokenizer.from_pretrained(base, trust_remote_code=False, **pinned)
    model = AutoModelForSequenceClassification.from_pretrained(
        base, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id={l: i for i, l in enumerate(LABELS)},
        use_safetensors=True, trust_remote_code=False, **pinned)
    device = device or pick_device(torch)
    model.to(device)
    print(f"  training on {device}", flush=True)

    # Swapped copies teach both directions: with the markers exchanged, A→B becomes B→A.
    examples = [(marked_text(f), label) for f, label in train] + \
               [(marked_text(f, swap=True), flip_label(label)) for f, label in train]
    counts = Counter(label for _, label in examples)
    weights = torch.tensor([len(examples) / (len(LABELS) * counts[l]) if counts[l] else 1.0 for l in LABELS])
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights.to(device))
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    generator = torch.Generator().manual_seed(seed)
    index = {l: i for i, l in enumerate(LABELS)}
    model.train()
    for epoch in range(epochs):
        order = torch.randperm(len(examples), generator=generator).tolist()
        total = 0.0
        for start in range(0, len(order), batch):
            chunk = [examples[i] for i in order[start:start + batch]]
            enc = tokenizer([t for t, _ in chunk], return_tensors="pt", **padding_for(device, MAX_LENGTH)).to(device)
            loss = loss_fn(model(**enc).logits, torch.tensor([index[l] for _, l in chunk], device=device))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += loss.item()
        print(f"  epoch {epoch + 1}/{epochs} loss {total / max(1, (len(order) + batch - 1) // batch):.4f}", flush=True)
    model.eval()

    valid_preds = _predict(model, tokenizer, [marked_text(f) for f, _ in valid], LABELS)
    tau = choose_threshold(valid_preds, [label for _, label in valid])
    test_gold = [label for _, label in test]
    test_preds = _predict(model, tokenizer, [marked_text(f) for f, _ in test], LABELS)
    precision, recall = _accepted_scores(test_preds, test_gold, tau)
    rule_preds = [(_rule_label(f), 1.0) for f, _ in test]
    rule_precision, rule_recall = _accepted_scores(rule_preds, test_gold, 0.0)
    per_class = {}
    for label in RELATION_LABELS:
        accepted = [g for (p, prob), g in zip(test_preds, test_gold) if p == label and prob >= tau - EPS]
        support = sum(g == label for g in test_gold)
        per_class[label] = {"accepted": len(accepted), "correct": sum(g == label for g in accepted), "support": support}
    confusion = [[sum(1 for (p, _), g in zip(test_preds, test_gold) if g == gl and p == pl) for pl in LABELS]
                 for gl in LABELS]

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.to("cpu").save_pretrained(out_dir, safe_serialization=True)
    tokenizer.save_pretrained(out_dir)
    (out_dir / "tau.json").write_text(json.dumps({"tau": tau, "labels": list(LABELS), "base": base,
                                                  "revision": revision}, ensure_ascii=False), encoding="utf-8")
    return {"tau": tau, "test_precision": precision, "test_recall": recall, "rule_precision": rule_precision,
            "rule_recall": rule_recall, "per_class": per_class, "confusion": confusion,
            "sizes": {"train": len(train), "valid": len(valid), "test": len(test)}}


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def render_relation_report(stats: dict) -> str:
    review = stats.get("review")
    review_line = "사람 검토 대기" if review is None else f"사람 검토 일치율 {_pct(review)}"
    lines = [
        "# NEXUS 관계 판별기 검증 보고서",
        "",
        "> `nexus train-relations`가 생성한 파일이다. 다시 돌리면 덮어쓴다.",
        "",
        f"- 기본 모델: `{MODEL_ID}` 리비전 `{REVISION}`",
        "- 정답 기준: 두 회사의 관계를 문장이 직접 말할 때만 관계, 제3자와 각각 관계가 있다는 나열은 관계 없음",
        "",
        "## 데이터",
        "",
        f"- 라벨 분포: " + ", ".join(f"{k} {v}" for k, v in sorted(stats.get("labels", {}).items())),
        f"- {review_line}",
    ]
    sizes = stats.get("sizes")
    if sizes:
        lines.append(f"- 분할(기업쌍 단위): 학습 {sizes['train']} · 검증 {sizes['valid']} · 시험 {sizes['test']}")
    lines += [
        "",
        "## 시험 구간",
        "",
        "| 방법 | 정확도(인정한 판별 중 맞은 비율) | 재현율(실제 관계 중 찾은 비율) |",
        "|---|---:|---:|",
        f"| KPF-BERT (τ 적용) | {_pct(stats.get('test_precision'))} | {_pct(stats.get('test_recall'))} |",
        f"| 규칙(서비스 기준) | {_pct(stats.get('rule_precision'))} | {_pct(stats.get('rule_recall'))} |",
        "",
    ]
    if stats.get("per_class"):
        lines += ["| 관계 | 인정 | 맞음 | 실제 |", "|---|---:|---:|---:|"]
        lines += [f"| {k} | {v['accepted']} | {v['correct']} | {v['support']} |" for k, v in stats["per_class"].items()]
        lines.append("")
    if stats.get("confusion"):
        lines += ["혼동 행렬(행=정답, 열=판별, τ 미적용):", "", "| 정답 \\ 판별 | " + " | ".join(LABELS) + " |",
                  "|---|" + "---:|" * len(LABELS)]
        lines += [f"| {g} | " + " | ".join(str(n) for n in row) + " |" for g, row in zip(LABELS, stats["confusion"])]
        lines.append("")
    lines += [
        "## 확신 기준",
        "",
        f"- τ = {stats.get('tau', 0):.2f} (검증 구간에서 인정한 판별의 정확도가 85% 이상이 되는 가장 작은 값, 없으면 0.95)",
        "",
        "## 적용 결과",
        "",
    ]
    before, after = stats.get("relations_before"), stats.get("relations_after")
    if stats.get("applied") is False:
        lines.append("- 시험 정확도가 85% 미만이라 모델을 적용하지 않았다. analyze는 규칙만 쓴다.")
    elif after is None:
        lines.append("- analyze 이후 채운다.")
    else:
        lines.append(f"- 근거 관계 {before} → {after}, 근거 관계가 있는 기업 {stats.get('companies_after')}개")
    lines += [
        "",
        "## 한계",
        "",
        "- 정답은 Claude가 붙였다. 사람 검토 일치율을 위에 적는다.",
        "- 학습 표본이 약 2,000문장이고, hilight가 200자 안팎으로 잘려 문맥이 짧다.",
        "- 시험 구간은 기업쌍 단위로 나눠 같은 쌍의 문장으로 점수가 부풀지 않게 했다.",
        "",
    ]
    return "\n".join(lines)
