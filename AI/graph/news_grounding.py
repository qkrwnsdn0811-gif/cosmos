"""Claim-level support checks for LLM paraphrases; no access to impact scores.

Every sentence cites ONE exact source span. Numbers must occur in that span;
cross-document date/number mixing is rejected before a frozen NLI verifier.
NLI is an additional heuristic (including cross-lingual Korean), not a factual
guarantee. On any failure the caller can return only verified exact quotations.
"""
import json
import re
from pathlib import Path
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


def parse_claims(raw, evidence):
    obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    if set(obj) != {"claims"} or not isinstance(obj["claims"], list) or not 1 <= len(obj["claims"]) <= 3:
        raise ValueError("invalid claim schema")
    sources = {e["id"]: e for e in evidence}
    for c in obj["claims"]:
        if set(c) != {"evidence_id", "quote", "summary"}:
            raise ValueError("invalid claim fields")
        source = sources[c["evidence_id"]]
        quote, summary = c["quote"], c["summary"]
        if not isinstance(quote, str) or not isinstance(summary, str):
            raise ValueError("claim must be text")
        if not 8 <= len(quote) <= 600 or quote not in source["text"] or not 8 <= len(summary) <= 300:
            raise ValueError("quote not in source or excessive summary")
        numbers = lambda s: set(re.findall(r"\d+(?:[.,]\d+)*", s))
        if not numbers(summary) <= numbers(quote):
            raise ValueError("unsupported date/number")
        # NLI can over-accept an agreement as proof of implementation. Preserve
        # this high-impact distinction independently for Korean source claims.
        if re.search(r"협약|MOU|양해각서", quote, re.I) and not re.search(r"협약|MOU|양해각서", summary, re.I):
            raise ValueError("agreement qualifier omitted")
        if any(w in quote for w in ("계획", "예정", "가능성")) and not any(w in summary for w in ("계획", "예정", "가능성")):
            raise ValueError("prospective qualifier omitted")
        if re.search(r"https?://|\[[^\]]+\]|<[^>]+>", summary):
            raise ValueError("generator may not add URLs/markup/citations")
        # Statistical edges cannot become contractual/causal claims. Render their
        # verified data verbatim instead of asking NLI to infer financial semantics.
        if source["kind"] in ("correlation", "co_mention") and summary != quote:
            raise ValueError("statistical evidence requires literal rendering")
    return obj["claims"]


class EntailmentVerifier:
    def __init__(self, out):
        self.out = Path(out)
        self.spec = json.loads((self.out / "grounding_model.json").read_text())
        self.model = self.tokenizer = None

    @torch.inference_mode()
    def verify(self, claims):
        if self.model is None:
            path = self.out / self.spec["path"].replace("\\", "/")
            self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
            # The model card warns against fp16. Keep float32 on CPU; generation
            # can independently use the GPU without holding two large GPU models.
            self.model = AutoModelForSequenceClassification.from_pretrained(path,
                local_files_only=True, trust_remote_code=False, dtype=torch.float32).eval()
        ids = {str(v).lower(): int(k) for k, v in self.model.config.id2label.items()}
        if set(ids) != {"entailment", "neutral", "contradiction"}:
            raise ValueError("unrecognized NLI labels")
        inputs = self.tokenizer([c["quote"] for c in claims], [c["summary"] for c in claims],
                                padding=True, truncation=False, return_tensors="pt")
        if inputs.input_ids.shape[1] > 512:
            return False, []  # Never silently truncate away a negation/qualifier.
        probs = self.model(**inputs).logits.softmax(-1).tolist()
        scores = [{name: float(p[i]) for name, i in ids.items()} for p in probs]
        good = all(s["entailment"] >= self.spec["entailment_threshold"] and
                   s["contradiction"] <= self.spec["contradiction_ceiling"] for s in scores)
        return good, scores
