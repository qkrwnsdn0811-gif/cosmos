"""Validate one company-mention batch and add pinned FinBERT sentiment.

The verdict is per (article, company) and is read off the sentences that name
that company and no other company from the same article.  An article-wide
title+lead label was the earlier design; it cannot say that one company in an
article did well while another did badly, which is the question the service
asks.  This is still financial polarity from a sentence classifier, not an
aspect-trained company model and not a stock-price forecast.

Every source row is preserved.  A company whose sentences disagree, or that has
no attributable sentence at all, keeps a NULL label: an invented neutral reads
on screen as "we analysed this and it was unremarkable", which is a different
and false claim.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable


VERSION = "news-finbert-enrichment-2.0"
# The same contract the backfill script publishes under: FinBERT over
# target-attributed evidence sentences with the unanimity rule.  One name for
# one contract is what lets a stored row be traced to how it was produced.
MODEL_VERSION = "evidence-sentence-finbert-v1"
LABEL_SCOPE = "target_attributed_sentence_financial_polarity"
AGGREGATION = "equal_weight_unique_sentences_positive_negative_conflict_null"
LABELS = ("negative", "neutral", "positive")
LANGUAGE_ROLES = {"ko": "sentiment_ko", "en": "sentiment_en"}
# Sentences, not articles: 128 word-piece tokens covers a news sentence, and a
# sentence long enough to be truncated is rare enough to record rather than hide.
MAX_LENGTH = 128
SOURCE_DATASETS = {
    "news-company-mentions",
    "news-historical-company-mentions",
}
# One list entry per matched company, so the article row stays 1:1 with the
# source row and the manifest's record count keeps meaning what it meant.
OUTPUT_FIELDS = (
    "ai_company_sentiment",
    "ai_sentiment_scope",
    "ai_enriched_at",
)
STATUS_ANALYZED = "analyzed"
STATUS_NO_EVIDENCE = "no_attributed_sentence"
STATUS_CONFLICT = "conflicting_polarity"
STATUS_TIED = "tied_probabilities"


class EnrichmentError(RuntimeError):
    pass


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _label_indexes(config: dict) -> dict[str, int]:
    raw = config.get("id2label")
    if not isinstance(raw, dict) or len(raw) != 3:
        raise EnrichmentError("sentiment model must declare three id2label values")
    try:
        labels = {str(value).lower(): int(key) for key, value in raw.items()}
    except (TypeError, ValueError) as error:
        raise EnrichmentError("invalid sentiment label mapping") from error
    if set(labels) != set(LABELS) or set(labels.values()) != {0, 1, 2}:
        raise EnrichmentError(f"unsupported sentiment labels: {raw}")
    return labels


def _label(probabilities) -> str | None:
    """Argmax over LABELS order, or None when two classes tie exactly.

    A tie is not a neutral result; it is the model declining to choose, and
    recording it as neutral would put a fabricated verdict on screen.
    """
    maximum = max(probabilities)
    winners = [LABELS[index] for index, value in enumerate(probabilities) if value == maximum]
    return winners[0].upper() if len(winners) == 1 else None


def load_model_specs(bundle: Path) -> tuple[dict, str]:
    bundle = bundle.resolve()
    receipt_path = bundle / "pretrained.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EnrichmentError("could not read model bundle receipt") from error
    specs = {}
    for role in LANGUAGE_ROLES.values():
        item = receipt.get(role)
        if not isinstance(item, dict):
            raise EnrichmentError(f"missing model role: {role}")
        relative, revision, repo = item.get("path"), item.get("revision"), item.get("repo")
        if (not isinstance(relative, str) or not isinstance(repo, str)
                or not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
            raise EnrichmentError(f"invalid pinned model receipt: {role}")
        model_path = (bundle / relative).resolve()
        if not model_path.is_relative_to(bundle) or not model_path.is_dir():
            raise EnrichmentError(f"model path is missing or outside bundle: {role}")
        config_path = model_path / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise EnrichmentError(f"invalid model config: {role}") from error
        files = {
            file.relative_to(model_path).as_posix(): file_sha256(file)
            for file in sorted(model_path.rglob("*")) if file.is_file()
        }
        specs[role] = {
            "path": str(model_path), "repo": repo, "revision": revision,
            "label_indexes": _label_indexes(config),
            "config_sha256": file_sha256(config_path),
            "files_manifest_sha256": sha256(canonical_json(files)),
        }
    return specs, file_sha256(receipt_path)


class FinbertEngine:
    """Long-lived lazy model cache shared by historical and realtime batches."""

    def __init__(self, bundle: Path, device: str = "cuda", batch_size: int = 64):
        if not re.fullmatch(r"cpu|cuda(?::\d+)?", device):
            raise ValueError("device must be cpu, cuda, or cuda:<index>")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.bundle = bundle.resolve()
        self.device = device
        self.batch_size = batch_size
        self.specs, self.receipt_sha256 = load_model_specs(self.bundle)
        self._models: dict[str, tuple] = {}
        public_specs = {
            role: {key: spec[key] for key in ("repo", "revision", "config_sha256",
                                               "files_manifest_sha256", "label_indexes")}
            for role, spec in self.specs.items()
        }
        self.provenance = {
            "version": VERSION, "model_version": MODEL_VERSION,
            "label_scope": LABEL_SCOPE, "max_length": MAX_LENGTH,
            "aggregation": AGGREGATION, "probabilities_order": list(LABELS),
            "model_receipt_sha256": self.receipt_sha256, "models": public_specs,
            "device": device, "local_files_only": True,
            "is_company_specific": True, "is_stock_price_prediction": False,
        }
        self.provenance_sha256 = sha256(canonical_json(self.provenance))

    def _load(self, role: str):
        if role in self._models:
            return self._models[role]
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                          HF_HUB_DISABLE_TELEMETRY="1")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        if self.device.startswith("cuda") and not torch.cuda.is_available():
            raise EnrichmentError("CUDA was requested but is unavailable")
        spec = self.specs[role]
        tokenizer = AutoTokenizer.from_pretrained(spec["path"], local_files_only=True,
                                                   trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(
            spec["path"], local_files_only=True, trust_remote_code=False,
        ).to(self.device).eval()
        labels = _label_indexes(model.config.to_dict())
        if labels != spec["label_indexes"]:
            raise EnrichmentError(f"loaded label mapping changed: {role}")
        value = (torch, tokenizer, model, [labels[label] for label in LABELS])
        self._models[role] = value
        return value

    def _classify(self, work: list[dict]) -> None:
        """Fill each work item's ``probabilities`` in place, batched per model."""
        selected: dict[str, list[int]] = {role: [] for role in LANGUAGE_ROLES.values()}
        for index, item in enumerate(work):
            selected[item["role"]].append(index)
        for role, indices in selected.items():
            if not indices:
                continue
            torch, tokenizer, model, order = self._load(role)
            with torch.inference_mode():
                for start in range(0, len(indices), self.batch_size):
                    ids = indices[start:start + self.batch_size]
                    inputs = tokenizer([work[index]["text"] for index in ids], padding=True,
                                       truncation=True, max_length=MAX_LENGTH,
                                       return_tensors="pt").to(self.device)
                    probabilities = model(**inputs).logits.softmax(-1)[:, order].cpu().tolist()
                    for index, values in zip(ids, probabilities):
                        values = [float(value) for value in values]
                        if (len(values) != 3 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in values)
                                or abs(sum(values) - 1) > 1e-5):
                            raise EnrichmentError("model produced invalid probabilities")
                        work[index]["probabilities"] = values

    def predict(self, rows: list[dict]) -> list[list[dict]]:
        """Return one per-company verdict list for each article row.

        Sentences are classified once across the whole batch and shared by every
        company that cites them, so an article naming five companies does not
        pay for its title five times.
        """
        from evidence import article_evidence

        per_row = [article_evidence(row.get("title") or "", row.get("content") or "",
                                    row.get("companies"))
                   for row in rows]
        work: list[dict] = []
        index_of: dict[tuple[int, str, str], int] = {}
        for row_index, companies in enumerate(per_row):
            for entry in companies:
                for sentence in entry["evidence"]:
                    # Unique by (article, language, text): a sentence repeated in
                    # the same article is one context, not two votes.
                    key = (row_index, sentence["language"], sentence["text"])
                    if key in index_of:
                        continue
                    index_of[key] = len(work)
                    work.append({"text": sentence["text"], "language": sentence["language"],
                                 "role": LANGUAGE_ROLES[sentence["language"]],
                                 "probabilities": None})
        self._classify(work)
        if any(item["probabilities"] is None for item in work):
            raise EnrichmentError("inference left unclassified sentences")

        results = []
        for row_index, companies in enumerate(per_row):
            verdicts = []
            for entry in companies:
                verdicts.append(self._aggregate(entry, row_index, work, index_of))
            results.append(verdicts)
        return results

    def _aggregate(self, entry: dict, row_index: int, work: list[dict],
                   index_of: dict) -> dict:
        """Collapse one company's sentences into a single verdict.

        Equal weight per unique sentence.  A tie inside one sentence, or a
        positive and a negative sentence in the same company, yields NULL: the
        rule is unanimity of polarity, never a majority vote, because two
        sentences pulling opposite ways is exactly the case where a single
        label would be a guess.
        """
        verdict = {
            "market": entry["market"], "stock_code": entry["stock_code"],
            "label": None, "score": None, "probabilities": None,
            "status": entry["status"], "n_sentences": 0,
            "model_role": None, "model_revision": None,
            "evidence": [],
        }
        if not entry["evidence"]:
            return verdict

        seen, contexts, evidence = set(), [], []
        for sentence in entry["evidence"]:
            key = (row_index, sentence["language"], sentence["text"])
            if key in seen:
                continue
            seen.add(key)
            values = work[index_of[key]]["probabilities"]
            contexts.append(values)
            evidence.append({
                "sentence_order": sentence["sentence_order"],
                "source": sentence["source"],
                "start": sentence["start"],
                "end": sentence["end"],
                "text": sentence["text"],
                "language": sentence["language"],
                "label": _label(values),
                # The model's own top-class probability for this sentence. It is
                # uncalibrated, so it ranks sentences against each other and is
                # not a probability of being correct.
                "confidence": max(values),
            })
        roles = {LANGUAGE_ROLES[item["language"]] for item in evidence}
        verdict.update(n_sentences=len(contexts), evidence=evidence,
                       model_role="+".join(sorted(roles)),
                       model_revision="+".join(sorted(
                           self.specs[role]["revision"] for role in roles)))

        labels = {item["label"] for item in evidence}
        if None in labels:
            verdict["status"] = STATUS_TIED
            return verdict
        if {"POSITIVE", "NEGATIVE"} <= labels:
            verdict["status"] = STATUS_CONFLICT
            return verdict
        # Neutral abstains. FinBERT puts a high neutral probability on most
        # sentences, so averaging every sentence together buries the direction
        # the article actually carries: one neutral [0.10, 0.75, 0.15] and one
        # positive [0.05, 0.40, 0.55] average to [0.08, 0.58, 0.35], which is
        # neutral. The more evidence a pair has, the more certainly it washes
        # out - measured in production 2026-09-23, the neutral share *rose*
        # with evidence, 37.3% at one sentence to 53.8% at four.
        #
        # So the directional sentences decide alone. Conflict is already gone
        # by here, so they all agree, and "삼성전자가 SK하이닉스에 HBM을
        # 공급한다" sitting beside "3분기 영업이익 기대치 상회" is not two
        # readings in tension - the first simply says nothing about direction.
        pool = [values for values, item in zip(contexts, evidence)
                if item["label"] != "NEUTRAL"] or contexts
        mean = [sum(values[position] for values in pool) / len(pool)
                for position in range(3)]
        label = _label(mean)
        if label is None:
            verdict["status"] = STATUS_TIED
            return verdict
        verdict.update(status=STATUS_ANALYZED, label=label, probabilities=mean,
                       score=mean[2] - mean[0])
        return verdict


def _source_inventory(manifest: dict) -> dict:
    if (manifest.get("version") != 1 or manifest.get("dataset") not in SOURCE_DATASETS
            or not isinstance(manifest.get("output"), dict)):
        raise EnrichmentError("unsupported source analysis manifest")
    files = manifest["output"].get("files")
    if not isinstance(files, list) or len(files) != 1 or files[0].get("path") != "data.parquet":
        raise EnrichmentError("source batch must declare exactly data.parquet")
    item = files[0]
    if (type(item.get("bytes")) is not int or item["bytes"] < 0
            or type(item.get("records")) is not int or item["records"] < 0
            or not isinstance(item.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
        raise EnrichmentError("invalid source Parquet inventory")
    return item


def enrich_local_batch(source_dir: Path, output_dir: Path, source_uri: str,
                       engine: FinbertEngine, analyzed_at: str | None = None) -> dict:
    """Create an immutable local output directory suitable for HDFS publication."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    source_dir, output_dir = source_dir.resolve(), output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {output_dir}")
    if (source_dir / "_SUCCESS").read_bytes() != b"":
        raise EnrichmentError("source _SUCCESS marker is invalid")
    manifest_bytes = (source_dir / "_manifest.json").read_bytes()
    try:
        source_manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EnrichmentError("source manifest is invalid JSON") from error
    inventory = _source_inventory(source_manifest)
    parquet_path = source_dir / "data.parquet"
    parquet_bytes = parquet_path.read_bytes()
    if len(parquet_bytes) != inventory["bytes"] or sha256(parquet_bytes) != inventory["sha256"]:
        raise EnrichmentError("source Parquet does not match manifest")
    parquet = pq.ParquetFile(pa.BufferReader(parquet_bytes))
    if parquet.metadata.num_rows != inventory["records"]:
        raise EnrichmentError("source Parquet row count does not match manifest")
    table = parquet.read()
    if any(field in table.column_names for field in OUTPUT_FIELDS):
        raise EnrichmentError("source already contains GPU enrichment fields")
    rows = table.to_pylist()
    predictions = engine.predict(rows)
    timestamp = analyzed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        timestamp = parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError) as error:
        raise EnrichmentError("analyzed_at must include a timezone") from error
    evidence_type = pa.struct([
        ("sentence_order", pa.int32()), ("source", pa.string()),
        ("start", pa.int32()), ("end", pa.int32()), ("text", pa.string()),
        ("language", pa.string()), ("label", pa.string()),
        ("confidence", pa.float64()),
    ])
    # A variable-length list, not list_(float64, 3): a fixed-size list holding a
    # null inside a struct writes a Parquet file that cannot be read back
    # ("Expected all lists to be of size=3 but index N had size=0"), and a
    # company with no attributable sentence has exactly that null. The three
    # values are guaranteed by _classify, which rejects anything else.
    verdict_type = pa.struct([
        ("market", pa.string()), ("stock_code", pa.string()),
        ("label", pa.string()), ("score", pa.float64()),
        ("probabilities", pa.list_(pa.float64())), ("status", pa.string()),
        ("n_sentences", pa.int32()), ("model_role", pa.string()),
        ("model_revision", pa.string()), ("evidence", pa.list_(evidence_type)),
    ])
    columns = {
        "ai_company_sentiment": pa.array(predictions, pa.list_(verdict_type)),
        "ai_sentiment_scope": pa.array([LABEL_SCOPE] * len(rows), pa.string()),
        "ai_enriched_at": pa.array([timestamp] * len(rows), pa.string()),
    }
    for name in OUTPUT_FIELDS:
        table = table.append_column(name, columns[name])
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="snappy")
    output_bytes = sink.getvalue().to_pybytes()
    if pq.ParquetFile(pa.BufferReader(output_bytes)).metadata.num_rows != len(rows):
        raise EnrichmentError("output Parquet verification failed")
    verdicts = [verdict for row in predictions for verdict in row]
    counts = {status: sum(verdict["status"] == status for verdict in verdicts)
              for status in (STATUS_ANALYZED, STATUS_NO_EVIDENCE,
                             STATUS_CONFLICT, STATUS_TIED)}
    counts["companies"] = len(verdicts)
    counts["sentences"] = sum(verdict["n_sentences"] for verdict in verdicts)
    identity = {
        "contract_version": VERSION, "source_uri": source_uri,
        "source_manifest_sha256": sha256(manifest_bytes),
        "source_data_sha256": inventory["sha256"],
        "source_analysis_id": source_manifest.get("analysis_id"),
        "model_version": MODEL_VERSION,
        "model_provenance_sha256": engine.provenance_sha256,
    }
    manifest = {
        "version": 1, "dataset": "news-ai-sentiment", "schema_version": VERSION,
        "analysis_id": sha256(canonical_json(identity)), "identity": identity,
        "model_version": MODEL_VERSION, "created_at": timestamp,
        "label_scope": LABEL_SCOPE, "model_provenance": engine.provenance,
        "input": {"uri": source_uri, "dataset": source_manifest["dataset"],
                  "manifest_sha256": sha256(manifest_bytes), "records": len(rows),
                  "file_sha256": inventory["sha256"]},
        "output": {"records": len(rows), "status_counts": counts,
                   "files": [{"path": "data.parquet", "bytes": len(output_bytes),
                              "sha256": sha256(output_bytes), "records": len(rows)}]},
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        (output_dir / "data.parquet").write_bytes(output_bytes)
        (output_dir / "_manifest.json").write_bytes(canonical_json(manifest) + b"\n")
        (output_dir / "_SUCCESS").write_bytes(b"")
    except Exception:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return manifest

