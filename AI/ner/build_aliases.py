"""Build the aliases table from companies.csv plus curated seed files.

Usage:
    python build_aliases.py [--companies data/companies.csv] [--seeds data/seeds] [--out data/aliases.csv]

Output columns:
    alias, ticker, name_official, market, alias_type, ambiguity, group_id, source, note

alias_type : official | official_en | en_rule | ko_manual | en_manual | mined | ticker | group | product | industry | blocker
ambiguity  : safe          -> emit when matched (after boundary rules)
             needs_context -> group / abbreviation; resolved by matcher context rules
             product       -> product/service name (페이스북, 아이폰); counts only if the company is also named
             industry      -> sector expression (반도체 업황, 조선업계); reported separately as an industry hit, no ticker
             ticker_only   -> only inside "(NVDA)", "$NVDA", "NASDAQ: NVDA"-style contexts
             blocked       -> never emit; consumes the span so shorter aliases inside are suppressed
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

CORP_SUFFIX_RE = re.compile(
    r"(,?\s*(co\.?,?\s*ltd\.?|co,\.ltd|co\.,?\s*ltd|company\s+limited|corporation|corp\.?|inc\.?|incorporated|"
    r"limited|ltd\.?|plc|holdings?|n\.v\.|s\.a\.|ag|group))+\s*$",
    re.IGNORECASE,
)
# 외래어 음차 흔들림: 영어 복수 -s 를 '즈'로 적은 표기와 '스'로 적은 표기가 둘 다 유통된다
# (시스코시스템즈/시스코시스템스, 베이커휴즈/베이커휴스). nasdaq_ko 별칭에 한해 '스' 표기를 파생한다.
# 국내 기업(kospi_manual)에는 절대 적용하지 마라 - '즈' 종결이 음차가 아니라 법인명이고
# (SK머티리얼즈, 카카오게임즈) '스' 표기는 실존하지 않는다.
KO_PLURAL_SUFFIX = "즈"
KO_PLURAL_MIN_LEN = 4  # '몬델리즈'(4자)가 하한. 3자 이하는 일반명사 충돌 위험이 크다

CLASS_RE = re.compile(r"\s*\((class\s+[a-z]|adr|ads)\)\s*", re.IGNORECASE)

# English words that are company names but too common to match alone.
EN_BANNED_ALONE = {
    "Alphabet", "Arm", "Booking", "Strategy", "Constellation", "Diamondback", "Roper", "Ross",
    "Vertex", "Monster", "Axon", "Cadence", "Microchip", "Intuitive", "Palo Alto", "Old Dominion",
    "Analog Devices", "Applied Materials", "Automatic Data Processing", "Monolithic Power Systems",
    "Take-Two Interactive", "Warner Bros. Discovery", "Meta", "Coca-Cola Europacific Partners",
    "American Electric Power", "Texas Instruments", "Marvell Technology", "Micron Technology",
    "Lam Research", "GE HealthCare", "Honeywell Aerospace", "Honeywell Technologies", "Idexx Laboratories",
    "Gilead Sciences", "Regeneron Pharmaceuticals", "Alnylam Pharmaceuticals", "Vertex Pharmaceuticals",
    "Marriott International", "Mondelez International", "Kraft Heinz", "Keurig Dr Pepper",
    "Palantir Technologies", "Palo Alto Networks", "Rocket Lab", "Seagate Technology", "Western Digital",
    "Xcel Energy", "Intuitive Surgical", "Cadence Design Systems", "Advanced Micro Devices",
    "Baker Hughes", "Astera Labs", "Nebius Group", "Axon Enterprise", "Fortinet",
}
# Suffix-stripped variants we still want even though the stripped form is short.
EN_SHORT_OK = {"KLA", "CSX", "ASML", "NXP", "PDD", "HMM", "Intel", "Apple", "Amazon", "Adobe", "Cisco",
               "Costco", "Nvidia", "Netflix", "Tesla", "Walmart", "Amgen", "Copart", "Cintas", "Exelon",
               "Linde", "Paccar", "Shopify", "Starbucks", "Synopsys", "Teradyne", "Qualcomm", "Lumentum",
               "Sandisk", "Intuit", "Autodesk", "Comcast", "Datadog", "DexCom", "DoorDash", "Fastenal",
               "Ferrovial", "Broadcom", "Airbnb", "AppLovin", "CoreWeave", "CrowdStrike", "PayPal",
               "PepsiCo", "SpaceX", "Workday", "Microsoft", "Palantir", "MicroStrategy", "Mercado Libre"}

DIGIT_TICKER_RE = re.compile(r"^\d{6}$")


def read_csv(path: Path) -> list[dict]:
    """Read a seed CSV, refusing to continue on a row that was silently truncated.

    An unquoted comma inside a field (``...;Strategy, Inc.``) makes DictReader drop the tail
    into restkey; the surviving value then looks like a legitimate alias ("Strategy"), so the
    corruption is invisible in aliases.csv. Fail loudly instead.
    """
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, restkey="__extra__"))
    for lineno, r in enumerate(rows, 2):
        if "__extra__" in r:
            raise ValueError(
                f"{path.name}:{lineno} has an unquoted comma and was truncated "
                f"(dropped: {r['__extra__']!r}). Wrap the field in double quotes."
            )
    return rows


def strip_corp(name: str) -> str:
    s = CLASS_RE.sub(" ", name).strip()
    prev = None
    while prev != s:
        prev = s
        s = CORP_SUFFIX_RE.sub("", s).strip().rstrip(",. ")
    return s


def title_case(s: str) -> str:
    return " ".join(w if (len(w) <= 3 and w.isupper()) else w.capitalize() for w in s.split())


def add(rows, seen, alias, ticker, official, market, alias_type, ambiguity, group_id="", source="rule", note=""):
    alias = alias.strip()
    if not alias or len(alias) < 2:
        return
    key = (alias, ticker)
    if key in seen:
        # a company whose name equals its ticker (AMD, ASML, CSX): the name alias must win over the
        # ticker-only rule, otherwise "AMD의 MI300" is never matched
        if ambiguity == "safe":
            for r in rows:
                if r["alias"] == alias and r["ticker"] == ticker and r["ambiguity"] == "ticker_only":
                    r.update({"alias_type": alias_type, "ambiguity": "safe", "source": source, "note": "name == ticker"})
        return
    seen.add(key)
    rows.append({
        "alias": alias, "ticker": ticker, "name_official": official, "market": market,
        "alias_type": alias_type, "ambiguity": ambiguity, "group_id": group_id,
        "source": source, "note": note,
    })


def build(companies_path: Path, seeds_dir: Path) -> tuple[list[dict], list[str]]:
    companies = read_csv(companies_path)
    by_name = {c["name_official"]: c for c in companies}
    by_ticker = {c["ticker"]: c for c in companies}

    groups = read_csv(seeds_dir / "groups.csv")
    group_alias = {g["alias"]: g for g in groups}

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    warnings: list[str] = []

    product_aliases = set()
    products_path = seeds_dir / "products.csv"
    if products_path.exists():
        product_aliases = {r["alias"].strip() for r in read_csv(products_path)}

    # 1. official names and rule-based variants
    for c in companies:
        t, off, mk, en = c["ticker"], c["name_official"].strip(), c["market"], c["name_en"].strip()
        if off in group_alias:
            # same-name holding company: handled as group alias below
            pass
        else:
            add(rows, seen, off, t, off, mk, "official", "safe")
            nospace = off.replace(" ", "")
            if nospace != off:
                add(rows, seen, nospace, t, off, mk, "official", "safe", note="space removed")
        if mk == "KOSPI" and en:
            base = strip_corp(en)
            if len(base) >= 3:
                add(rows, seen, base, t, off, mk, "official_en", "safe", source="dart")
                tc = title_case(base)
                if tc != base:
                    add(rows, seen, tc, t, off, mk, "en_rule", "safe", note="title case of DART name")
        if mk == "NASDAQ":
            base = strip_corp(off)
            if base != off and base not in EN_BANNED_ALONE and (len(base) >= 5 or base in EN_SHORT_OK):
                add(rows, seen, base, t, off, mk, "en_rule", "safe", note="corporate suffix stripped")
        # ticker itself: only inside parentheses / $ / exchange-prefix contexts
        add(rows, seen, t, t, off, mk, "ticker", "ticker_only")

    # 2. curated Korean names for NASDAQ
    for r in read_csv(seeds_dir / "nasdaq_ko.csv"):
        t = r["ticker"].strip()
        if t not in by_ticker:
            warnings.append(f"nasdaq_ko: unknown ticker {t}")
            continue
        c = by_ticker[t]
        for a in filter(None, (x.strip() for x in r["ko_names"].split(";"))):
            if a in product_aliases:
                continue  # handled as product below
            if a in EN_BANNED_ALONE:
                warnings.append(f"nasdaq_ko: {t} alias {a!r} is a common word alone - skipped")
                continue
            amb = "needs_context" if a in group_alias else "safe"
            add(rows, seen, a, t, c["name_official"], c["market"], "ko_manual", amb, source="manual")
            if " " in a:
                add(rows, seen, a.replace(" ", ""), t, c["name_official"], c["market"], "ko_manual", amb,
                    source="manual", note="space removed")
            # '시스코시스템즈' -> '시스코시스템스'. 이 블록 안에 있어야 안전하다: blockers(5번)와
            # products(4b)가 뒤에 실행돼 last-wins 로 파생형을 덮어쓴다.
            if amb == "safe" and len(a) >= KO_PLURAL_MIN_LEN and a.endswith(KO_PLURAL_SUFFIX):
                v = a[:-1] + "스"
                add(rows, seen, v, t, c["name_official"], c["market"], "ko_manual", "safe",
                    source="rule", note="즈->스 음차 변형 자동 파생")
                if " " in v:
                    add(rows, seen, v.replace(" ", ""), t, c["name_official"], c["market"], "ko_manual",
                        "safe", source="rule", note="즈->스 음차 변형, 공백 제거")

    # 3. curated aliases for KOSPI (Korean nicknames, English names, old names, bank subsidiaries)
    for r in read_csv(seeds_dir / "kospi_manual.csv"):
        off = r["name_official"].strip()
        if off not in by_name:
            warnings.append(f"kospi_manual: unknown name_official {off}")
            continue
        c = by_name[off]
        for a in filter(None, (x.strip() for x in r["aliases"].split(";"))):
            if a in group_alias:
                continue
            is_ko = bool(re.search(r"[가-힣]", a))
            add(rows, seen, a, c["ticker"], off, c["market"], "ko_manual" if is_ko else "en_manual",
                "safe", source="manual", note=r.get("note", ""))

    # 4. group / abbreviation aliases needing context
    for g in groups:
        default = g["default_name_official"].strip()
        ticker, market = "", ""
        if default:
            if default in by_name:
                ticker, market = by_name[default]["ticker"], by_name[default]["market"]
            elif default in by_ticker:
                ticker, market = default, by_ticker[default]["market"]
            else:
                warnings.append(f"groups: unknown default {default}")
        gid = g["group_id"]
        official = default
        if not default and gid in by_ticker:  # e.g. META / ARM / GOOGL / MSFT
            ticker, market, official = gid, by_ticker[gid]["market"], by_ticker[gid]["name_official"]
        add(rows, seen, g["alias"], ticker, official, market, "group", "needs_context", gid, "manual", g.get("note", ""))

    # 4b. product / service names: count only when the company itself is also named in the document
    products_path = seeds_dir / "products.csv"
    if products_path.exists():
        for r in read_csv(products_path):
            t = r["ticker"].strip()
            if t not in by_ticker:
                warnings.append(f"products: unknown ticker {t}")
                continue
            c = by_ticker[t]
            add(rows, seen, r["alias"], t, c["name_official"], c["market"], "product", "product",
                source="manual", note=r.get("note", ""))

    # 4c. industry / sector expressions ("반도체 업황", "조선업계"): not a company, but the signal that lets
    #     company-free industry news reach the industry node in the knowledge graph
    industries_path = seeds_dir / "industries.csv"
    if industries_path.exists():
        for r in read_csv(industries_path):
            for a in filter(None, (x.strip() for x in r["aliases"].split(";"))):
                if a in group_alias or a in product_aliases:
                    warnings.append(f"industries: alias '{a}' collides with group/product alias, skipped")
                    continue
                add(rows, seen, a, "", r["name_ko"], "", "industry", "industry", r["industry_id"], "manual")

    # 5. blockers
    for line in (seeds_dir / "blockers.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        add(rows, seen, line, "", "", "", "blocker", "blocked", source="manual")

    # 6. conflict check: one alias -> several tickers
    by_alias = defaultdict(set)
    for r in rows:
        if r["alias_type"] not in ("blocker", "industry"):
            by_alias[r["alias"]].add(r["ticker"])
    for a, ts in by_alias.items():
        if len(ts) > 1:
            warnings.append(f"conflict: alias '{a}' maps to {sorted(ts)}")
    return rows, warnings


def main():
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--companies", default=here / "data" / "companies.csv", type=Path)
    ap.add_argument("--seeds", default=here / "data" / "seeds", type=Path)
    ap.add_argument("--out", default=here / "data" / "aliases.csv", type=Path)
    args = ap.parse_args()

    rows, warnings = build(args.companies, args.seeds)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print(f"wrote {len(rows)} aliases -> {args.out}")
    print("by type:", dict(Counter(r["alias_type"] for r in rows)))
    print("by ambiguity:", dict(Counter(r["ambiguity"] for r in rows)))
    for wmsg in warnings:
        print("WARN", wmsg)


if __name__ == "__main__":
    main()
