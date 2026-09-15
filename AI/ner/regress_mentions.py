"""v1.3 수정용 회귀 도구 — 전문 200건에 매처를 돌려 멘션 스냅샷을 뜨고 이전 스냅샷과 비교한다.

  # 고치기 전에 기준선을 뜬다
  PYTHONUTF8=1 .venv/Scripts/python.exe regress_mentions.py --out /tmp/before.json
  # matcher.py / aliases.csv 를 고친 뒤
  PYTHONUTF8=1 .venv/Scripts/python.exe regress_mentions.py --baseline /tmp/before.json

왜 이게 필요한가: dict-v1.2 검수 표본(sample_v12.jsonl)은 본문이 2500자로 잘린 읽기용
산출물이라 보일러플레이트 절단·나열문 미탐처럼 본문 뒷부분에 걸린 규칙을 로컬에서 확인할
수 없다. data/eval/v12_audit/full200.jsonl 은 같은 200건의 원문(최대 13,249자)이고
fetch_full_bodies.py 로 HDFS 에서 다시 받을 수 있다.

배치는 사전 기반(dict-v1.2)이므로 여기서도 NER 없이 CompanyMatcher 만 돌린다. 7.5시간짜리
재배치를 돌리기 전에 각 수정이 무엇을 지우고 무엇을 새로 만드는지 여기서 먼저 본다.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from matcher import CompanyMatcher

HERE = Path(__file__).resolve().parent
FULL200 = HERE / "data" / "eval" / "v12_audit" / "full200.jsonl"


def snapshot(docs: list[dict], aliases: Path) -> dict[str, list]:
    m = CompanyMatcher.from_csv(aliases)
    out = {}
    for r in docs:
        res = m.match(r["title"] or "", r["body"] or "")
        out[r["record_id"]] = sorted([x.ticker, x.start, x.alias, round(x.confidence, 2)]
                                     for x in res.mentions)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", default=FULL200, type=Path)
    ap.add_argument("--aliases", default=HERE / "data" / "aliases.csv", type=Path)
    ap.add_argument("--out", type=Path, help="스냅샷 저장 경로")
    ap.add_argument("--baseline", type=Path, help="이 스냅샷과 비교")
    ap.add_argument("--show", type=int, default=12, help="변화 문서 몇 건까지 출력")
    args = ap.parse_args()

    if not args.docs.exists():
        raise SystemExit(f"{args.docs} 가 없습니다. fetch_full_bodies.py 로 서버에서 받아오세요 "
                         f"(docstring 참고).")
    docs = [json.loads(l) for l in args.docs.open(encoding="utf-8")]
    titles = {r["record_id"]: (r.get("title") or "")[:46] for r in docs}
    cur = snapshot(docs, args.aliases)
    total = sum(len(v) for v in cur.values())
    print(f"문서 {len(cur)} | 멘션 {total}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
        print(f"  -> {args.out}")

    if not args.baseline:
        return
    old = json.loads(args.baseline.read_text(encoding="utf-8"))
    key = lambda rows: {(r[0], r[1]) for r in rows}          # (ticker, start)
    alias_of = {(r[0], r[1]): r[2] for rows in cur.values() for r in rows}
    alias_of.update({(r[0], r[1]): r[2] for rows in old.values() for r in rows})

    removed, added, changed = Counter(), Counter(), []
    for rid in old.keys() | cur.keys():
        o, n = key(old.get(rid, [])), key(cur.get(rid, []))
        if o == n:
            continue
        changed.append((rid, o - n, n - o))
        for t, s in o - n:
            removed[t] += 1
        for t, s in n - o:
            added[t] += 1
    print(f"기준선 {sum(len(v) for v in old.values())} -> 현재 {total}  "
          f"| 제거 {sum(removed.values())} / 추가 {sum(added.values())} (문서 {len(changed)})")
    if removed:
        print("  제거 상위:", ", ".join(f"{t}×{n}" for t, n in removed.most_common(8)))
    if added:
        print("  추가 상위:", ", ".join(f"{t}×{n}" for t, n in added.most_common(8)))
    for rid, rem, add in sorted(changed, key=lambda x: -(len(x[1]) + len(x[2])))[:args.show]:
        bits = []
        if rem:
            bits.append("-" + ",".join(sorted({f"{t}({alias_of.get((t, s), '')})" for t, s in rem})[:4]))
        if add:
            bits.append("+" + ",".join(sorted({f"{t}({alias_of.get((t, s), '')})" for t, s in add})[:4]))
        print(f"  {rid[:8]} {titles.get(rid, ''):<48} {' '.join(bits)}")


if __name__ == "__main__":
    main()
