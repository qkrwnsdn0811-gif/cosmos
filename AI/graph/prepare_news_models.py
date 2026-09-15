"""Pin and download public pretrained encoders and the local explanation LLM.

Only model/tokenizer files are downloaded; remote code is never enabled.
Weights stay in ignored artifacts/. Models are frozen in the impact task.
The commit revisions are recorded for repeatable training and serving.
"""
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

HERE = Path(__file__).resolve().parent
MODELS = {
    "embedding": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "sentiment_ko": "snunlp/KR-FinBert-SC",
    "sentiment_en": "ProsusAI/finbert",
    "explanation": "Qwen/Qwen2.5-1.5B-Instruct",
}


def main():
    out = HERE / "artifacts/news_impact"
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "pretrained.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for role, repo in MODELS.items():
        info = HfApi().model_info(repo, revision=manifest.get(role, {}).get("revision", "main"))
        files = [f.rfilename for f in info.siblings]
        weights = "*.safetensors" if "model.safetensors" in files else "pytorch_model.bin"
        path = snapshot_download(repo, revision=info.sha, cache_dir=out / "hf", allow_patterns=[
            "config.json", "generation_config.json", "tokenizer*", "vocab*", "merges.txt",
            "special_tokens_map.json", "added_tokens.json", "sentencepiece*", weights])
        manifest[role] = {"repo": repo, "revision": info.sha, "path": str(Path(path).relative_to(out))}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf8")
        print(role, repo, info.sha, flush=True)


if __name__ == "__main__":
    main()
