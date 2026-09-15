"""Download a pinned multilingual NLI verifier for grounded paraphrases.

Separate manifest: explanation changes do not invalidate frozen price-model
features. The verifier is a fallible filter, never proof of factual accuracy.
Korean is a cross-lingual transfer language for this model; evaluate it locally.
"""
import argparse
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
from news_impact_data import OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    args.out = args.out.resolve()
    path = args.out / "grounding_model.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    repo = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    info = HfApi().model_info(repo, revision=old.get("revision", "main"))
    cached = snapshot_download(repo, revision=info.sha, cache_dir=args.out / "hf",
        allow_patterns=["config.json", "tokenizer*", "special_tokens_map.json", "spm.model", "model.safetensors"])
    spec = {"repo": repo, "revision": info.sha, "path": str(Path(cached).relative_to(args.out)).replace("\\", "/"),
            "entailment_threshold": .90, "contradiction_ceiling": .05}
    path.write_text(json.dumps(spec, indent=2), encoding="utf8")
    print(json.dumps(spec), flush=True)


if __name__ == "__main__":
    main()
