"""rel_scores_v2.json → 운영 public 스키마 적재용 CSV 4종.

  PYTHONUTF8=1 python build_full_load_package.py

산출물은 data/db_full_20260922/ 에 들어간다. 같은 폴더의 load_full_reprocess.sql 이
이 네 파일을 \\copy 로 읽는다. 적재 방법과 되돌리기는 그 SQL 머리말에 적어 뒀다.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from build_relationship_seed import RELATIONSHIP_TYPES
from relationship_score_components import FORMULA_VERSION, NATURAL_FIELDS, SCORE_FIELDS

HERE = Path(__file__).resolve().parent
OUT = HERE / "data/db_full_20260922"
MODEL_VERSION = "dict-v1.3+finbert-evidence-v1"
HDFS_URI = "hdfs://localhost:9000/data-lake/sandbox/news/junwoo/company-sentiment/v1"


def main() -> None:
    rows = json.loads((HERE / "data/rel_scores_v2.json").read_text(encoding="utf-8"))
    as_of = rows[0]["as_of_at"]
    if any(r["as_of_at"] != as_of or r["formula_version"] != FORMULA_VERSION for r in rows):
        raise ValueError("as_of_at·formula_version 이 한 판으로 통일돼 있지 않다")

    relationships = {tuple(r[f] for f in NATURAL_FIELDS): {f: r[f] for f in NATURAL_FIELDS}
                     for r in rows}
    OUT.mkdir(parents=True, exist_ok=True)

    def write(name: str, fields: list[str], values) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)   # None -> 따옴표 없는 빈 칸 -> PostgreSQL NULL

    write("relationship_type.csv", ["code", "name", "directionality"],
          [dict(zip(("code", "name", "directionality"), r)) for r in RELATIONSHIP_TYPES])
    write("graph_snapshot.csv",
          ["as_of_at", "formula_version", "model_version", "status", "hdfs_uri", "published_at"],
          [{"as_of_at": as_of, "formula_version": FORMULA_VERSION,
            "model_version": MODEL_VERSION, "status": "PUBLISHED",
            "hdfs_uri": HDFS_URI, "published_at": as_of}])
    write("company_relationship.csv", NATURAL_FIELDS, relationships.values())
    write("relationship_score_current.csv", SCORE_FIELDS, rows)

    print(f"{OUT}")
    print(f"  관계 {len(relationships):,} · 점수 {len(rows):,} · as_of {as_of} · {FORMULA_VERSION}")


if __name__ == "__main__":
    main()
