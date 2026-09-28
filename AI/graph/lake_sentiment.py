"""Offline article-title/lead FinBERT sentiment for cleaned news JSONL.

This is ARTICLE sentiment from sanitized title + '\\n' + body[:200], tokenized
to 128 wordpieces, exactly as FrozenText.sentiment preprocesses each article.
It is neither company-specific sentiment nor a predicted stock-price direction.
Language selection deliberately differs from old FrozenText: only explicit ko/en
(including their locale tags) select a model; region never determines language.

Example:
  python lake_sentiment.py --input articles.jsonl --output sentiment.jsonl \
    --bundle artifacts/news_impact_v2_bundle --device cuda --batch-size 32

The output has one row per input document_id, including null-valued rows with
status unsupported_language/empty_text. probs3 is [negative, neutral, positive].
Existing output/report files are refused. <output>.report.json records input and
output SHA256, declared pinned model revisions, config hashes, and skipped IDs.
No news_impact_data, price datasets, embedding, GNN, or explanation model loads.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from news_content import sanitize_article


LABELS = ("negative", "neutral", "positive")
LANGUAGE_ROLES = {"ko": "sentiment_ko", "en": "sentiment_en"}
LABEL_SCOPE = "article_title_lead"
MAX_LENGTH = 128
LEAD_CHARS = 200


def label_indexes(config: dict) -> dict[str, int]:
    """Validate actual model label IDs; never guess the English FinBERT order."""
    raw = config.get("id2label")
    if not isinstance(raw, dict) or len(raw) != 3:
        raise ValueError("Expected id2label with exactly three sentiment classes")
    try:
        labels = {str(value).lower(): int(key) for key, value in raw.items()}
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid id2label indices") from error
    if set(labels) != set(LABELS) or set(labels.values()) != {0, 1, 2}:
        raise ValueError(f"Unrecognized sentiment label mapping: {raw}")
    inverse = config.get("label2id")
    if inverse is not None:
        try:
            inverse = {str(key).lower(): int(value) for key, value in inverse.items()}
        except (AttributeError, TypeError, ValueError) as error:
            raise ValueError("Invalid label2id mapping") from error
        if inverse != labels:
            raise ValueError("label2id disagrees with id2label")
    if config.get("num_labels", 3) != 3:
        raise ValueError("Expected exactly three output labels")
    return labels


def explicit_language(value) -> str | None:
    """Accept explicit ISO language codes and locale tags, never infer from text."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower().replace("_", "-")
    if re.fullmatch(r"(?:ko|en)(?:-[a-z0-9]{2,8})*", value):
        return value.split("-", 1)[0]
    return None


def sentiment_text(article: dict) -> str:
    cleaned = sanitize_article(article)
    return (cleaned.get("title") or "") + "\n" + (cleaned.get("body") or "")[:LEAD_CHARS]


def prediction_row(document_id: str, language: str, role: str, revision: str, values) -> dict:
    probabilities = [float(value) for value in values]
    if len(probabilities) != 3 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError(f"Invalid probabilities for {document_id}")
    if abs(sum(probabilities) - 1.0) > 1e-5:
        raise ValueError(f"Probabilities do not sum to one for {document_id}")
    return {
        "document_id": document_id,
        "label": LABELS[max(range(3), key=lambda index: probabilities[index])].upper(),
        "score": probabilities[2] - probabilities[0], "probs3": probabilities,
        "model_role": role, "model_revision": revision, "status": "ok",
        "label_scope": LABEL_SCOPE, "language": language,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_specs(bundle: Path) -> tuple[dict, str]:
    bundle = bundle.resolve()
    pretrained_path = bundle / "pretrained.json"
    pretrained = json.loads(pretrained_path.read_text(encoding="utf-8-sig"))
    specs = {}
    for role in LANGUAGE_ROLES.values():
        source = pretrained.get(role)
        if not isinstance(source, dict):
            raise ValueError(f"Missing pretrained.json role: {role}")
        relative = source.get("path")
        revision, repo = source.get("revision"), source.get("repo")
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError(f"Missing local model path for {role}")
        relative = Path(relative.replace("\\", "/"))
        if relative.is_absolute():
            raise ValueError(f"Model path must be relative to the bundle: {role}")
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle) or not path.is_dir():
            raise ValueError(f"Model path is missing or outside the bundle: {role}")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError(f"Model revision must be a pinned 40-character commit SHA: {role}")
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError(f"Missing model repository identifier: {role}")
        config_path = path / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        specs[role] = {
            "path": str(path), "bundle_relative_path": path.relative_to(bundle).as_posix(),
            "repo": repo, "revision": revision, "label_indexes": label_indexes(config),
            "config_sha256": _sha256(config_path),
        }
    return specs, _sha256(pretrained_path)


def load_articles(path: Path) -> tuple[list[dict], str]:
    """Hash exactly the bytes read and reject duplicates before loading a model."""
    articles, seen, digest = [], set(), hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            digest.update(raw)
            line = raw.decode("utf-8-sig" if line_number == 1 else "utf-8")
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON at input line {line_number}") from error
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object at input line {line_number}")
            document_id = row.get("document_id")
            if not isinstance(document_id, str) or not document_id.strip() or document_id != document_id.strip():
                raise ValueError(f"Missing/invalid document_id at input line {line_number}")
            if document_id in seen:
                raise ValueError(f"Duplicate document_id at input line {line_number}: {document_id}")
            seen.add(document_id)
            for field in ("title", "body"):
                if row.get(field) is not None and not isinstance(row[field], str):
                    raise ValueError(f"{field} must be text at input line {line_number}")
            articles.append({
                "document_id": document_id, "title": row.get("title") or "",
                "body": row.get("body") or "", "region": row.get("region"),
                "language": row.get("language"),
            })
    return articles, digest.hexdigest()


def _write_atomic(path: Path, lines) -> str:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=path.name + ".", suffix=".tmp", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            for line in lines:
                stream.write(line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        digest = _sha256(temporary)
        # Paths belong to one batch run. Recheck immediately before publication.
        if path.exists():
            raise FileExistsError(f"Output appeared during inference: {path}")
        temporary.rename(path)
        temporary = None
        return digest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(input_path: Path, output_path: Path, bundle: Path, device: str = "cuda", batch_size: int = 32) -> dict:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if not re.fullmatch(r"cpu|cuda(?::\d+)?", device):
        raise ValueError("device must be cpu, cuda, or cuda:<index>")
    input_path, output_path, bundle = input_path.resolve(), output_path.resolve(), bundle.resolve()
    report_path = Path(str(output_path) + ".report.json")
    if output_path.exists() or report_path.exists():
        raise FileExistsError("Output or report already exists; choose a new batch output path")
    if input_path in (output_path, report_path):
        raise ValueError("Input and output/report must have different paths")
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    began = time.monotonic()
    articles, input_sha256 = load_articles(input_path)
    specs, pretrained_sha256 = load_model_specs(bundle)
    results, selected, skipped, texts = [], {role: [] for role in LANGUAGE_ROLES.values()}, [], {}
    for index, article in enumerate(articles):
        language = explicit_language(article.get("language"))
        role = LANGUAGE_ROLES.get(language)
        status = "unsupported_language" if role is None else "pending"
        if role is not None:
            text = sentiment_text(article)
            if not text.strip():
                status = "empty_text"
            else:
                texts[index] = text
                selected[role].append(index)
        results.append({
            "document_id": article["document_id"], "label": None, "score": None, "probs3": None,
            "model_role": role, "model_revision": specs[role]["revision"] if role else None,
            "status": status, "label_scope": LABEL_SCOPE, "language": language,
        })
        if status != "pending":
            skipped.append({"document_id": article["document_id"], "status": status,
                            "input_language": article.get("language"), "region": article.get("region")})

    runtime = {}
    if any(selected.values()):
        # Set offline controls BEFORE transformers import or model construction.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        import torch
        import transformers
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable; select --device cpu explicitly if desired")
        torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
        runtime = {"torch": torch.__version__, "transformers": transformers.__version__, "device": device}
        if device.startswith("cuda"):
            runtime["device_name"] = torch.cuda.get_device_name(torch.device(device))
        for role, indices in selected.items():
            if not indices:
                continue
            spec = specs[role]
            print(f"Loading {role} ({len(indices)} articles) offline", flush=True)
            tokenizer = AutoTokenizer.from_pretrained(spec["path"], local_files_only=True, trust_remote_code=False)
            model = AutoModelForSequenceClassification.from_pretrained(
                spec["path"], local_files_only=True, trust_remote_code=False,
            ).to(device).eval()
            try:
                labels = label_indexes(model.config.to_dict())
                if labels != spec["label_indexes"]:
                    raise ValueError(f"Loaded label order differs from bundled config: {role}")
                order = [labels[label] for label in LABELS]
                with torch.inference_mode():
                    for start in range(0, len(indices), batch_size):
                        ids = indices[start:start + batch_size]
                        inputs = tokenizer([texts[index] for index in ids], padding=True, truncation=True,
                                           max_length=MAX_LENGTH, return_tensors="pt").to(device)
                        logits = model(**inputs).logits
                        if logits.ndim != 2 or logits.shape[1] != 3:
                            raise ValueError(f"Unexpected classifier output shape: {tuple(logits.shape)}")
                        probabilities = logits.softmax(-1)[:, order].cpu().tolist()
                        for index, values in zip(ids, probabilities):
                            results[index] = prediction_row(
                                articles[index]["document_id"], explicit_language(articles[index]["language"]),
                                role, spec["revision"], values,
                            )
                        del inputs, logits
                        if start % (batch_size * 50) == 0 or start + batch_size >= len(indices):
                            print(f"{role} {min(start + batch_size, len(indices))}/{len(indices)}", flush=True)
            finally:
                del model, tokenizer
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

    if any(row["status"] == "pending" for row in results):
        raise RuntimeError("Inference left incomplete output rows")
    output_sha256 = _write_atomic(output_path, (
        json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n" for row in results
    ))
    counts = {status: sum(row["status"] == status for row in results) for status in ("ok", "unsupported_language", "empty_text")}
    report = {
        "schema_version": "lake-article-sentiment-1", "label_scope": LABEL_SCOPE,
        "input": str(input_path), "output": str(output_path), "bundle": str(bundle),
        "input_sha256": input_sha256, "output_sha256": output_sha256,
        "pretrained_sha256": pretrained_sha256, "input_rows": len(articles), "output_rows": len(results),
        "status_counts": counts, "models": specs, "model_revision_source": "bundle/pretrained.json",
        "models_used": [role for role, indices in selected.items() if indices],
        "preprocessing": {"sanitize_article": True, "body_characters": LEAD_CHARS,
                          "max_length": MAX_LENGTH, "truncation": True, "padding": True,
                          "text": "sanitized_title + newline + sanitized_body[:200]",
                          "language_selection": "explicit ko/en locale tags only; no region fallback",
                          "probs3_order": list(LABELS)},
        "implementation_sha256": _sha256(Path(__file__)),
        "sanitizer_sha256": _sha256(Path(__file__).with_name("news_content.py")),
        "batch_size": batch_size, "runtime": runtime, "skipped": skipped,
        "started_at": started, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - began, 3),
    }
    _write_atomic(report_path, [json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"])
    print(json.dumps({"output": str(output_path), "report": str(report_path), **counts}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    run(args.input, args.output, args.bundle, args.device, args.batch_size)


if __name__ == "__main__":
    main()
