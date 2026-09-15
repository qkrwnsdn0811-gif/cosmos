"""Compare dictionary matcher vs fine-tuned NER on the evaluation set: what does NER find that the dictionary misses?

  PYTHONUTF8=1 .venv/Scripts/python.exe compare_ner_dict.py --model models/ner-company-v1 [--eval data/eval/eval_set_200.jsonl]

For each article: dictionary spans (confidence 1.0) vs NER spans. An NER span that does not overlap any dictionary
span (or blocked/unresolved span) is a "new candidate" — the input to Step 7 entity linking. Prints coverage stats
and the most frequent new surface forms so we can see whether they are real companies (link them), aliases we
should add to the dictionary, or false positives.
"""
import argparse
import collections
import json
from pathlib import Path

from matcher import CompanyMatcher
from predict_ner import NerPredictor

HERE = Path(__file__).resolve().parent


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=HERE / "models" / "ner-company-v1")
    ap.add_argument("--eval", default=HERE / "data" / "eval" / "eval_set_200.jsonl")
    ap.add_argument("--min-score", type=float, default=0.7)
    ap.add_argument("--max-chars", type=int, default=3000, help="NER runs on title + first N chars of body (speed)")
    args = ap.parse_args()

    m = CompanyMatcher.from_csv(HERE / "data" / "aliases.csv")
    ner = NerPredictor(str(args.model))
    docs = [json.loads(l) for l in open(args.eval, encoding="utf-8")]

    n_dict = n_ner = n_both = n_ner_only = n_dict_only = 0
    new_forms = collections.Counter()
    known_missed = collections.Counter()   # dictionary found it, NER did not
    examples = collections.defaultdict(list)
    for d in docs:
        title, body = d.get("title") or "", (d.get("body") or "")[: args.max_chars]
        r = m.match(title, body)
        text = r.text
        dict_spans = [(x.start, x.end, x.alias) for x in r.mentions if x.confidence >= 1.0]
        other_spans = [(u.start, u.end) for u in r.unresolved] + [(b[1], b[2]) for b in r.blocked]
        # NER sentence by sentence (max_len 256 tokens); offsets shifted back to document coordinates
        ner_spans = []
        pos = 0
        for chunk in text.split("\n"):
            if chunk.strip():
                for s in ner.predict(chunk, min_score=args.min_score):
                    ner_spans.append((pos + s["start"], pos + s["end"], s["text"], s["score"]))
            pos += len(chunk) + 1
        n_dict += len(dict_spans)
        n_ner += len(ner_spans)
        for s in ner_spans:
            if any(overlaps(s, ds) for ds in dict_spans):
                n_both += 1
            elif any(overlaps(s, o) for o in other_spans):
                pass  # group name / product / blocked: dictionary already knows about it
            else:
                n_ner_only += 1
                new_forms[s[2]] += 1
                if len(examples[s[2]]) < 2:
                    examples[s[2]].append(text[max(0, s[0] - 30):s[1] + 30].replace("\n", " "))
        for ds in dict_spans:
            if not any(overlaps(ds, s) for s in ner_spans):
                n_dict_only += 1
                known_missed[ds[2]] += 1

    print(f"docs={len(docs)} dict_spans={n_dict} ner_spans={n_ner} agree={n_both} ner_only={n_ner_only} dict_only={n_dict_only}")
    print(f"NER recall of dictionary spans: {n_both / max(n_dict, 1):.3f}")
    print("\n== NER-only surface forms (candidates for linking / new aliases / false positives) ==")
    for form, c in new_forms.most_common(40):
        print(f"  {c:3}  {form!r:28} e.g. …{examples[form][0]}…")
    print("\n== dictionary spans NER missed ==", known_missed.most_common(15))


if __name__ == "__main__":
    main()
