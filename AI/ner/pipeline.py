"""Step 7 — the complete company-extraction pipeline: dictionary matcher + NER + entity linking, merged per ticker.

    from pipeline import CompanyExtractor
    ex = CompanyExtractor(aliases="data/aliases.csv", model="models/ner-company-v2")
    out = ex.extract(news_id, title, body)
    out["companies"]   -> [{news_id, ticker, name, n_mentions, first_pos, confidence, method, aliases}]  (final output format)
    out["industries"]  -> [{industry_id, industry_ko, n_mentions, first_pos}]
    out["unlinked"]    -> NER surface forms that could not be linked (dictionary-growth candidates, Step 9)
    out["dropped"]     -> NER spans rejected as org/sports/blocked (for audit)

Merge policy:
  - dictionary hits (conf 1.0 / 0.9 ticker / 0.7 product / 0.5 group-default) are authoritative
  - an NER span that overlaps a dictionary/blocked/unresolved span is ignored (dictionary already decided)
  - a remaining NER span goes to the linker: exact -> 0.8, product -> 0.6, group_default -> 0.4, fuzzy -> 0.6*score
  - per ticker: n_mentions = dict + ner mentions, confidence = max, method = "+"-joined set (dict|rule|product|ner)

CLI demo:
    PYTHONUTF8=1 .venv/Scripts/python.exe pipeline.py --eval data/eval/eval_set_200.jsonl --limit 5
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from linker import EntityLinker
from matcher import CompanyMatcher, mask_inserted_headlines

HERE = Path(__file__).resolve().parent
NER_CONF = {"exact": 0.8, "product": 0.6, "group_default": 0.4}


def _overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


class CompanyExtractor:
    def __init__(self, aliases=HERE / "data" / "aliases.csv", model=HERE / "models" / "ner-company-v2",
                 ner_min_score: float = 0.7, ner_max_chars: int = 6000, use_ner: bool = True, expand: bool = True):
        self.matcher = CompanyMatcher.from_csv(aliases)
        self.linker = EntityLinker.from_csv(aliases)
        self.expander = None
        if expand:
            from impact import ImpactExpander
            self.expander = ImpactExpander()
        self.ner = None
        if use_ner and not Path(model).exists():
            # models/ is gitignored (440MB), so a fresh clone has no weights. Degrade to the
            # dictionary instead of dying inside transformers with a "Repo id" error.
            print(f"[CompanyExtractor] NER 모델 폴더가 없어 사전 전용으로 동작합니다: {model}", file=sys.stderr)
            print("                  (가중치는 git에 없습니다. README의 재생성/내려받기 절차 참고)", file=sys.stderr)
            use_ner = False
        if use_ner:
            from predict_ner import NerPredictor  # lazy: torch is heavy
            self.ner = NerPredictor(str(model))
        self.use_ner = use_ner
        self.ner_min_score = ner_min_score
        self.ner_max_chars = ner_max_chars

    def _ner_spans(self, text: str):
        if self.ner is None:
            return []
        out, pos = [], 0
        for chunk in text[: self.ner_max_chars].split("\n"):
            if chunk.strip():
                for s in self.ner.predict(chunk, min_score=self.ner_min_score):
                    out.append((pos + s["start"], pos + s["end"], s["text"], s["score"]))
            pos += len(chunk) + 1
        return out

    def extract(self, news_id: str, title: str | None, body: str | None) -> dict:
        r = self.matcher.match(title, body)
        text = r.text
        taken = [(m.start, m.end) for m in r.mentions] + [(u.start, u.end) for u in r.unresolved] + \
                [(b[1], b[2]) for b in r.blocked] + [(i.start, i.end) for i in r.industries]

        agg: dict[str, dict] = {}

        def add(ticker, name, start, conf, method, alias):
            a = agg.setdefault(ticker, {"news_id": news_id, "ticker": ticker, "name": name, "n_mentions": 0,
                                        "first_pos": r.position_of(start), "first_start": start, "confidence": 0.0,
                                        "method": set(), "aliases": set()})
            a["n_mentions"] += 1
            a["confidence"] = max(a["confidence"], conf)
            a["method"].add(method)
            a["aliases"].add(alias)
            if start < a["first_start"]:
                a["first_start"], a["first_pos"] = start, r.position_of(start)

        for m in r.mentions:
            add(m.ticker, m.name_official, m.start, m.confidence, m.method, m.alias)

        unlinked, dropped = collections.Counter(), collections.Counter()
        # same boilerplate cut and inserted-headline masking as the matcher (offsets are preserved)
        ner_text = mask_inserted_headlines(text[: r.trimmed_at] if r.trimmed_at is not None else text)
        for start, end, surface, score in self._ner_spans(ner_text):
            if any(_overlaps((start, end), t) for t in taken):
                continue
            lk = self.linker.link(surface)
            if lk.ticker:
                conf = NER_CONF.get(lk.method, 0.6 * lk.score if lk.method == "fuzzy" else 0.5)
                add(lk.ticker, lk.name_official, start, round(conf, 3), "ner", surface)
            elif lk.reason == "unknown":
                unlinked[surface] += 1
            else:
                dropped[(surface, lk.reason)] += 1

        companies = []
        for a in sorted(agg.values(), key=lambda x: x["first_start"]):
            companies.append({"news_id": a["news_id"], "ticker": a["ticker"], "name": a["name"], "n_mentions": a["n_mentions"],
                              "first_pos": a["first_pos"], "confidence": round(a["confidence"], 3),
                              "method": "+".join(sorted(a["method"])), "aliases": sorted(a["aliases"])})
        result = {"news_id": news_id, "companies": companies, "industries": r.by_industry(),
                  "unlinked": dict(unlinked), "dropped": {f"{k[0]}|{k[1]}": v for k, v in dropped.items()},
                  "unresolved_groups": sorted({u.alias for u in r.unresolved if u.group_id != "PRODUCT"}),
                  "is_sports": r.is_sports}
        if self.expander is not None:
            # who is exposed and why: direct mention / industry keyword / graph neighbour (see impact.py)
            result["affected"] = self.expander.expand(companies, result["industries"])
        return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", default=HERE / "data" / "eval" / "eval_set_200.jsonl")
    ap.add_argument("--model", default=HERE / "models" / "ner-company-v2")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    ex = CompanyExtractor(model=args.model)
    docs = [json.loads(l) for l in open(args.eval, encoding="utf-8")]
    if args.limit:
        docs = docs[: args.limit]
    method_c, unl, drop = collections.Counter(), collections.Counter(), collections.Counter()
    ner_added_docs = 0
    outf = open(args.out, "w", encoding="utf-8") if args.out else None
    for d in docs:
        o = ex.extract(d.get("eval_id") or d["record_id"], d.get("title"), d.get("body"))
        if outf:
            outf.write(json.dumps(o, ensure_ascii=False) + "\n")
        ner_here = False
        for c in o["companies"]:
            method_c[c["method"]] += 1
            if "ner" in c["method"]:
                ner_here = True
        ner_added_docs += ner_here
        unl.update(o["unlinked"])
        drop.update(o["dropped"])
        if args.limit:
            print(f"\n## {o['news_id']} | {(d.get('title') or '')[:70]}")
            for c in o["companies"]:
                print(f"   {c['ticker']:>7} {c['name']:<14} x{c['n_mentions']} {c['first_pos']:<5} conf={c['confidence']} [{c['method']}] {c['aliases']}")
            if o["industries"]:
                print("   industries:", [(i["industry_id"], i["n_mentions"]) for i in o["industries"]])
            if o["unlinked"]:
                print("   unlinked:", o["unlinked"])
            if o["dropped"]:
                print("   dropped:", o["dropped"])
    print(f"\ndocs={len(docs)} companies_by_method={dict(method_c)} docs_where_ner_added_a_company={ner_added_docs}")
    print("unlinked (dictionary-growth candidates):", unl.most_common(25))
    print("dropped:", drop.most_common(15))
    if outf:
        outf.close()


if __name__ == "__main__":
    main()
