"""Run the fine-tuned NER model on text and return character-offset COMPANY spans.

    from predict_ner import NerPredictor
    p = NerPredictor("models/ner-company-v1")
    p.predict("삼성電子와 하이닉스가 HBM 공급을 늘린다")  -> [{"text": "삼성電子", "start": 0, "end": 4, "score": 0.98}, ...]

CLI smoke test:
    PYTHONUTF8=1 .venv/Scripts/python.exe predict_ner.py models/ner-company-v1 "엔비디아와 삼전이 협력한다"

This is the "NER 보완 탐지" stage: spans that the dictionary already covers are dropped by the caller
(see pipeline.py, Step 7); what remains are candidate new surface forms for entity linking.
"""
import sys

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer


class NerPredictor:
    def __init__(self, model_dir: str, device: str | None = None, max_len: int = 256):
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModelForTokenClassification.from_pretrained(model_dir).eval()
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.id2label = self.model.config.id2label
        self.max_len = max_len

    @torch.no_grad()
    def predict(self, text: str, min_score: float = 0.5) -> list[dict]:
        enc = self.tokenizer(text, return_offsets_mapping=True, truncation=True, max_length=self.max_len,
                             return_tensors="pt")
        offsets = enc.pop("offset_mapping")[0].tolist()
        logits = self.model(**{k: v.to(self.device) for k, v in enc.items()}).logits[0]
        probs = torch.softmax(logits, -1)
        pred = probs.argmax(-1).tolist()
        spans, cur = [], None
        for (s, e), lab_id, pr in zip(offsets, pred, probs.tolist()):
            if s == e:
                continue
            lab = self.id2label[lab_id]
            if lab == "B-COMPANY" or (lab == "I-COMPANY" and cur is None):
                if cur:
                    spans.append(cur)
                cur = {"start": s, "end": e, "scores": [pr[lab_id]]}
            elif lab == "I-COMPANY" and cur is not None:
                cur["end"] = e
                cur["scores"].append(pr[lab_id])
            else:
                if cur:
                    spans.append(cur)
                cur = None
        if cur:
            spans.append(cur)
        out = []
        for sp in spans:
            score = sum(sp["scores"]) / len(sp["scores"])
            if score >= min_score:
                out.append({"text": text[sp["start"]:sp["end"]], "start": sp["start"], "end": sp["end"],
                            "score": round(score, 4)})
        return out


if __name__ == "__main__":
    p = NerPredictor(sys.argv[1])
    for t in sys.argv[2:]:
        print(t, "->", p.predict(t))
