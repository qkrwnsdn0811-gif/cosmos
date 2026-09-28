"""Download the two pinned FinBERT classifiers and write an offline receipt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


MODELS = {
    "sentiment_en": ("ProsusAI/finbert", "4556d13015211d73dccd3fdd39d39232506f3e43"),
    "sentiment_ko": ("snunlp/KR-FinBert-SC", "f8586286cc3161fb648e9fee09a456069fd846d0"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    from huggingface_hub import snapshot_download

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = {}
    for role, (repo, revision) in MODELS.items():
        destination = output / role
        snapshot_download(repo_id=repo, revision=revision, local_dir=destination)
        receipt[role] = {"repo": repo, "revision": revision, "path": role}
    target = output / "pretrained.json"
    temporary = output / "pretrained.json.tmp"
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

