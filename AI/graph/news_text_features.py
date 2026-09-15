"""Frozen multilingual text embeddings and financial sentiment features.

Embedding: title plus lead/body truncated to 256 wordpieces, masked mean
pooling, L2 normalization. This intentionally bounded first version does not
claim full long-document understanding. Korean and English financial sentiment
models consume title plus first 200 body characters (128 wordpieces).
All model revisions and the SAME preprocessing are reused during serving.
Classifier label ordering is read from config, never guessed by index.
"""
from __future__ import annotations
import gc
import hashlib
import json
from pathlib import Path
import numpy as np
import torch
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
from news_impact_data import OUT
from news_content import sanitize_article


def feature_manifest(out=OUT):
    out = Path(out)
    paths = [out / "articles.jsonl", out / "pretrained.json", Path(__file__), Path(__file__).with_name("news_content.py")]
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class FrozenText:
    def __init__(self, out=OUT, device="cpu"):
        self.out, self.device = Path(out), device
        self.spec = json.loads((self.out / "pretrained.json").read_text())
        self.loaded = {}

    def get(self, role):
        if role not in self.loaded:
            path = self.out / self.spec[role]["path"].replace("\\", "/")
            tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True, trust_remote_code=False)
            factory = AutoModel if role == "embedding" else AutoModelForSequenceClassification
            model = factory.from_pretrained(path, local_files_only=True, trust_remote_code=False).to(self.device).eval()
            self.loaded[role] = tokenizer, model
        return self.loaded[role]

    def release(self):
        self.loaded.clear()
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    @torch.inference_mode()
    def embedding(self, articles, batch_size=32):
        articles = [sanitize_article(r) for r in articles]
        tokenizer, model = self.get("embedding")
        result = []
        for start in range(0, len(articles), batch_size):
            text = [(r.get("title") or "") + "\n" + (r.get("body") or "")[:6000]
                    for r in articles[start:start + batch_size]]
            inputs = tokenizer(text, padding=True, truncation=True, max_length=256, return_tensors="pt").to(self.device)
            hidden = model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            result.append(torch.nn.functional.normalize(pooled, dim=-1).cpu().numpy())
            if start % 2048 == 0:
                print(f"embedding {start}/{len(articles)}", flush=True)
        return np.concatenate(result).astype("float32")

    @torch.inference_mode()
    def sentiment(self, articles, batch_size=32, release_after=True):
        articles = [sanitize_article(r) for r in articles]
        result = np.zeros((len(articles), 3), dtype="float32")  # negative, neutral, positive
        for role, is_ko in (("sentiment_ko", True), ("sentiment_en", False)):
            indices = [i for i, r in enumerate(articles) if (r["region"] == "domestic") == is_ko]
            if not indices:
                continue
            tokenizer, model = self.get(role)
            labels = {str(v).lower(): int(k) for k, v in model.config.id2label.items()}
            if set(labels) != {"negative", "neutral", "positive"}:
                raise ValueError(f"Unrecognized sentiment labels for {role}: {labels}")
            for start in range(0, len(indices), batch_size):
                ids = indices[start:start + batch_size]
                text = [(articles[i].get("title") or "") + "\n" + (articles[i].get("body") or "")[:200] for i in ids]
                inputs = tokenizer(text, padding=True, truncation=True, max_length=128, return_tensors="pt").to(self.device)
                probs = model(**inputs).logits.softmax(-1).cpu().numpy()
                result[ids] = probs[:, [labels[k] for k in ("negative", "neutral", "positive")]]
                if start % 2048 == 0:
                    print(f"{role} {start}/{len(indices)}", flush=True)
            # Do not hold both sentiment models on the laptop GPU.
            if release_after:
                del model, tokenizer
                self.release()
        return result


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    out = args.out
    torch.set_num_threads(4)
    docs = [json.loads(s) for s in (out / "articles.jsonl").open(encoding="utf8")]
    enc = FrozenText(out, device="cuda" if torch.cuda.is_available() else "cpu")
    np.save(out / "embeddings.npy", enc.embedding(docs))
    enc.release()
    np.save(out / "sentiment.npy", enc.sentiment(docs))
    enc.release()
    (out / "feature_manifest.json").write_text(json.dumps(feature_manifest(out), indent=2), encoding="utf8")
    print("saved text features", len(docs), flush=True)


if __name__ == "__main__":
    main()
