"""Step 6 — fine-tune a token-classification model (B/I-COMPANY) on the distant-supervision corpus.

  PYTHONUTF8=1 .venv/Scripts/python.exe train_ner.py --corpus data/ner_corpus_v1.jsonl \
      --model klue/roberta-base --out models/ner-company-v1 --epochs 2 --batch 16 --max-len 128

Runs on the local RTX 4050 (6 GB) with fp16. Character-offset entities are aligned to word pieces through the
tokenizer's offset mapping; sub-tokens that continue a word inherit I-COMPANY; special tokens get -100.
"""
import argparse
import json
import os
import random
from pathlib import Path

LABELS = ["O", "B-COMPANY", "I-COMPANY"]
L2I = {l: i for i, l in enumerate(LABELS)}


def load_corpus(path):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    return {k: [r for r in rows if r["split"] == k] for k in ("train", "dev", "test")}


def encode(examples, tokenizer, max_len):
    enc = tokenizer(examples["text"], truncation=True, max_length=max_len, return_offsets_mapping=True)
    all_labels = []
    for i, offsets in enumerate(enc["offset_mapping"]):
        ents = sorted((e["start"], e["end"]) for e in examples["entities"][i])
        labels = []
        for (s, e) in offsets:
            if s == e:  # special token
                labels.append(-100)
                continue
            lab = "O"
            for a, b in ents:
                if s >= a and e <= b:
                    lab = "B-COMPANY" if s == a else "I-COMPANY"
                    break
                if s < a < e:  # token straddles the entity start (rare with wordpiece); treat as B
                    lab = "B-COMPANY"
                    break
            labels.append(L2I[lab])
        all_labels.append(labels)
    enc["labels"] = all_labels
    enc.pop("offset_mapping")
    return enc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--model", default="klue/roberta-base")
    ap.add_argument("--out", default="models/ner-company-v1")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--limit", type=int, default=0, help="debug: cap train rows")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import numpy as np
    import torch
    from datasets import Dataset
    from seqeval.metrics import classification_report, f1_score, precision_score, recall_score
    from transformers import (AutoModelForTokenClassification, AutoTokenizer, DataCollatorForTokenClassification,
                              Trainer, TrainingArguments)

    random.seed(args.seed)
    splits = load_corpus(args.corpus)
    if args.limit:
        splits = {k: v[: args.limit if k == "train" else max(200, args.limit // 5)] for k, v in splits.items()}
    print({k: len(v) for k, v in splits.items()})

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    ds = {k: Dataset.from_list(v).map(lambda ex: encode(ex, tokenizer, args.max_len), batched=True,
                                      remove_columns=["record_id", "region", "year", "text", "entities", "split"])
          for k, v in splits.items()}

    model = AutoModelForTokenClassification.from_pretrained(
        args.model, num_labels=len(LABELS), id2label=dict(enumerate(LABELS)), label2id=L2I)

    def compute_metrics(p):
        preds = np.argmax(p.predictions, axis=-1)
        y_true, y_pred = [], []
        for pr, lb in zip(preds, p.label_ids):
            t, q = [], []
            for a, b in zip(pr, lb):
                if b == -100:
                    continue
                t.append(LABELS[b]); q.append(LABELS[a])
            y_true.append(t); y_pred.append(q)
        return {"precision": precision_score(y_true, y_pred), "recall": recall_score(y_true, y_pred),
                "f1": f1_score(y_true, y_pred)}

    use_cuda = torch.cuda.is_available()
    total_steps = max(1, int(len(ds["train"]) / args.batch * args.epochs))
    targs = TrainingArguments(
        output_dir=args.out, num_train_epochs=args.epochs, learning_rate=args.lr,
        per_device_train_batch_size=args.batch, per_device_eval_batch_size=args.batch * 2,
        warmup_steps=int(total_steps * 0.06), weight_decay=0.01, fp16=use_cuda, eval_strategy="epoch", save_strategy="epoch",
        save_total_limit=1, load_best_model_at_end=True, metric_for_best_model="f1", logging_steps=100,
        report_to=[], seed=args.seed, dataloader_num_workers=0,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=ds["train"], eval_dataset=ds["dev"],
                      data_collator=DataCollatorForTokenClassification(tokenizer), processing_class=tokenizer,
                      compute_metrics=compute_metrics)
    trainer.train()
    test_metrics = trainer.evaluate(ds["test"], metric_key_prefix="test")
    print(test_metrics)
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    Path(args.out, "train_summary.json").write_text(json.dumps({
        "base_model": args.model, "corpus": os.path.basename(args.corpus), "sizes": {k: len(v) for k, v in splits.items()},
        "epochs": args.epochs, "batch": args.batch, "max_len": args.max_len, "lr": args.lr,
        "test_metrics": test_metrics, "labels": LABELS, "note": "distant supervision labels; see make_bio_dataset.py",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
