"""Produce a self-contained inference artifact bundle with verified file hashes.

Frozen HF snapshots are copied into the bundle (no parent-directory references).
Training labels/prices are excluded; archived articles are needed for GraphRAG.
Never overwrites an existing bundle. No server upload or production deployment.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def package(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("Choose a fresh bundle destination")
    destination.mkdir(parents=True)
    required = ["metadata.json", "companies.json", "graphs.json", "text_projection.npz", "label_config.npz", "articles.jsonl", "aliases.csv"]
    for name in required:
        shutil.copy2(source / name, destination / name)
    for pattern in ("gat_seed*.pt", "lightgbm_*.txt"):
        for path in source.glob(pattern):
            shutil.copy2(path, destination / path.name)
    for name in ("pretrained.json", "grounding_model.json"):
        original = json.loads((source / name).read_text())
        specs = original if name == "pretrained.json" else {"grounding": original}
        for role, spec in specs.items():
            snapshot = (source / spec["path"].replace("\\", "/")).resolve()
            target = destination / "models" / role
            target.mkdir(parents=True)
            for path in snapshot.iterdir():
                if path.is_file():
                    shutil.copy2(path, target / path.name, follow_symlinks=True)
            spec["path"] = f"models/{role}"
        (destination / name).write_text(json.dumps(original, indent=2), encoding="utf8")
    manifest = {str(p.relative_to(destination)).replace("\\", "/"): {"bytes": p.stat().st_size,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in destination.rglob("*") if p.is_file()}
    for name in ("pretrained.json", "grounding_model.json"):
        specs = json.loads((destination / name).read_text())
        for spec in (specs.values() if name == "pretrained.json" else [specs]):
            (destination / spec["path"]).resolve().relative_to(destination)
    (destination / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
    print(json.dumps({"bundle": str(destination), "files": len(manifest), "bytes": sum(v["bytes"] for v in manifest.values())}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--destination", type=Path, required=True)
    args = ap.parse_args()
    package(args.source, args.destination)
