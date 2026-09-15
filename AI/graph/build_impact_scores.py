"""Story 3 계수표 기준선 — 당일 영향도 계수. 이전 표 기반 출하안을 재현한다.

소스 기업 기반 순위 비교는 train_impact_u3.py, 원래 명세의 뉴스 텍스트 입력 모델은
train_news_impact.py에 있다. 이 스크립트는 기존 계수표 결과의 재현용이다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_impact_scores.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe build_impact_scores.py --refit train+val

왜 모델이 아니라 표인가
  같은 실행의 KOSPI 공통 이벤트 9,486건에서 pair_shrunk 표의 test IC 는 0.0838,
  GNN 3홉은 재실행에서 0.0795 였다 (차이 t=-0.89, HANDOFF_STORY3.md §2-3). 별도로 학습하는 신경망 없이
  train 가격으로 추정한 표도 강한 기준선이다. 이 스크립트는 표 후보를 val 로 선택한다.
  두 시장의 최신 head-to-head 결과와 짝지은 차이 검정은 train_impact_gnn.py 에 기록한다.

영향도 계수의 정의
  뉴스로 A 가 x_A 만큼(비정상 수익률, 시그마 단위) 움직였을 때
      B 의 예상 영향 = coef(A -> B) * x_A
  coef 의 부호가 방향(같이 오르나 반대로 가나), 크기가 세기다.
  이건 예측이 아니라 **귀속**이다 — event_study_propagation.py 에서 다음 세션 전파가 0으로
  나왔으므로 "내일 오를 종목"으로 읽으면 안 된다. "지금 같이 흔들린 종목"이다.

후보 (전부 train 구간에서만 추정, val 로 선택, test 는 마지막에 한 번)
  industry        같은 업종이면 1, 아니면 0            ← 파라미터 0개 기준선
  industry_signed 같은 업종 1, 단 impact_direction=NEGATIVE 인 쌍만 -1
  industry_pair   (소스업종, 타깃업종)별 평균 잔차 상관   ← 18x18 표. "반도체 -> 장비는 크게,
                  반도체 -> 은행은 거의 안 옮김"을 표현할 수 있다. 같은 업종 규칙의 일반화다.
  pair_corr       쌍별 잔차 상관                        ← 가장 유연하지만 쌍마다 표본이 적다
  pair_shrunk     alpha * 쌍별 + (1-alpha) * 업종쌍 평균 ← val 로 고른 alpha=0.5
  relation_score  관계 쌍에 부호 ±1, 나머지는 0         ← 현재 coef_matrix 구현

평가 세 가지. 제품이 묻는 것과 논문이 묻는 것이 다르다.
  U1 전체 횡단면 : 타깃 = 그 시장 전 종목, 정답 = 잔차. GNN 과 과제 정의는 같지만,
                   공통 이벤트를 정하는 모델 구성이 달라 별도 실행 숫자를 직접 비교하면 안 된다.
                   표와 GNN 의 직접 비교는 train_impact_gnn.py 의 같은 실행에서 한다.
  U2 그래프 이웃 : 타깃 = A 와 관계 엣지로 이어진 종목만. **화면이 실제로 보여주는 것**이
                   이쪽이다. 사용자는 202종목 순위를 보는 게 아니라 "이 회사와 엮인 회사들"의
                   순위를 본다. U1 에서 관계 엣지가 꼴찌였다고 U2 에서도 쓸모없다는 뜻은 아니다
                   — 후보 집합을 관계가 이미 정해준 뒤의 순위 문제라서 질문이 다르다.
  U3 이웃·부호없음: 타깃 = 관계 이웃, 정답 = |잔차|, 점수 = |계수| * |소스 충격|.
                   **화면이 영향받을 기업 순위만 보여준다면 이 평가가 맞다.** 소스 충격은
                   모든 후보에 같은 스칼라이므로 순위 서빙에는 실시간 가격이 필요 없다.

실측 (KOSPI test rank-IC, HANDOFF_STORY3.md §2-2)
  규칙             U1       U2       U3
  industry         0.0725   0.2034   0.1135
  industry_pair    0.0572   0.1487   0.0418
  pair_shrunk      0.0762   0.2013   0.1017
  pair_corr        0.0727   0.2154   0.2054
  기본 --target unsigned 는 U3 의 val 로 pair_corr 를 선택한다. U3 에서 업종규칙과의
  짝지은 차이는 +0.1317 (세션 클러스터 t=+11.34) 이다. 표의 개별 IC 는 규칙별 유효
  이벤트 수가 다를 수 있어 단순히 빼면 이 짝지은 차이와 다르다.
  NASDAQ U2/U3 의 주요 규칙은 이벤트가 340~515건뿐이다. pair_corr 의 업종규칙 대비
  t 는 U2 +0.21, U3 +0.64 로 우위를 확인하지 못했다. U3 pair_shrunk 는 t=-2.72 로
  나쁘므로, "모든 t 가 2 미만"이라고 요약하면 안 된다.

산출물
  impact_coefficients_relation.csv: 관계 엣지 568행, 제품용.
  impact_coefficients_top.csv: |계수|>=0.15, 확장용.
  impact_coefficients.csv: 전체 35,532행, 진단용. 제품에 그대로 쓰지 않는다.
  평가 계수는 train 으로만 추정하고, 내보낼 표는 기본 --refit train+val 로 다시 추정한다.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
import pandas as pd

from impact_dataset import (HERE, build_panel, coef_matrix, load_relation_edges,
                            train_corr)
from train_impact_gnn import aligned_tensors, event_ic, paired_t

DIRECTION_CSV = HERE / "data" / "db" / "relationship_impact_direction.csv"
OUT = HERE / "data" / "impact_coefficients.csv"
REPORT = HERE / "data" / "impact_scores_eval.csv"
MARKETS = ("KOSPI", "NASDAQ")
FIELDS = ["src_ticker", "dst_ticker", "src_market", "dst_market", "coefficient",
          "impact_direction", "basis", "src_industry", "dst_industry", "n_train_days",
          "pair_corr", "rule", "fit_window"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-z", type=float, default=2.0)
    ap.add_argument("--refit", choices=("train", "train+val"), default="train+val",
                    help="최종 산출 계수를 어느 구간으로 다시 추정할지. 선택·평가는 항상 "
                         "train 추정 + val 선택 + test 보고로 하고, 내보낼 표만 이걸로 다시 잡는다")
    ap.add_argument("--out", default=OUT, type=Path)
    ap.add_argument("--report", default=REPORT, type=Path)
    ap.add_argument("--target", choices=("unsigned", "signed"), default="unsigned",
                    help="제품이 무엇을 보여주는가에 맞춰 규칙을 고른다. unsigned=\"영향받을 "
                         "기업\" 목록만 보여줌(U3 로 선택), signed=\"오를/내릴\" 을 가름(U1 로 "
                         "선택). 화면이 순위만 보여준다면 unsigned 가 맞고, 그 경우 서빙에 "
                         "가격이 전혀 필요 없다")
    ap.add_argument("--min-abs-coef", type=float, default=0.15,
                    help="확장용 표에 남길 |계수| 하한. 0.15 에서 같은 업종 비율 76%%, 음수 "
                         "11%% 로 잡음이 대부분 걸러진다 (0.10 이면 45%%/38%%)")
    args = ap.parse_args()

    panel = build_panel(beta_mode="prior", factors="market")
    A = aligned_tensors(panel)
    tk = panel.tickers
    pos = {t: i for i, t in enumerate(tk)}

    direction: dict[tuple[str, str], str] = {}
    if DIRECTION_CSV.exists():
        for r in pd.read_csv(DIRECTION_CSV, dtype=str).itertuples(index=False):
            direction[(r.source_stock_code, r.target_stock_code)] = r.impact_direction

    rel = load_relation_edges(as_of=None)
    neighbors: dict[str, set[str]] = {}
    for v in rel.itertuples(index=False):
        neighbors.setdefault(v.src, set()).add(v.dst)

    tr_rows = panel.split.to_numpy() == "train"
    sd = np.nanstd(panel.resid.to_numpy()[tr_rows], axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, 1.0)

    # 이벤트 정의는 GNN 과 같다. 직접 비교하려면 모델별 공통 이벤트 집합도 같아야 한다.
    ev = {}
    for mkt in MARKETS:
        d = A[mkt]
        Xz, Yz = d["X"] / sd[None, :], d["Y"] / sd[None, d["cols"]]
        split = panel.split.to_numpy()[d["rows"]]
        i_idx, j_idx = np.nonzero(np.isfinite(Xz) & (np.abs(Xz) >= args.event_z))
        local = {c: k for k, c in enumerate(d["cols"])}
        truth, valid = np.nan_to_num(Yz[i_idx]), np.isfinite(Yz[i_idx])
        for r, j in enumerate(j_idx):
            if j in local:
                valid[r, local[j]] = False        # 소스 자신은 채점 대상이 아니다
        # U2: 관계 엣지로 이어진 타깃만
        nb = np.zeros_like(valid)
        for r, j in enumerate(j_idx):
            for t in neighbors.get(tk[j], ()):
                if t in pos and pos[t] in local:
                    nb[r, local[pos[t]]] = True
        ev[mkt] = {"x": Xz[i_idx, j_idx], "src": j_idx, "truth": truth, "valid": valid,
                   "valid_nb": valid & nb, "split": split[i_idx], "cols": d["cols"],
                   "sess": i_idx}

    same_tr, cross_tr = train_corr(panel, {"train"})

    def scores(W: np.ndarray, mkt: str, idx: np.ndarray) -> np.ndarray:
        e = ev[mkt]
        return W[np.ix_(e["src"][idx], A[mkt]["cols"])] * e["x"][idx][:, None]

    # 수축 비율은 val 로 고른다. 0=업종쌍 평균, 1=쌍별 상관 그대로.
    def val_score(W: np.ndarray) -> float:
        vs = []
        for mkt in MARKETS:
            e = ev[mkt]
            idx = np.flatnonzero(e["split"] == "val")
            vs.append(event_ic(scores(W, mkt, idx), e["truth"][idx], e["valid"][idx])[0])
        return float(np.nanmean(vs))

    alphas = [0.0, 0.25, 0.5, 0.75, 1.0]
    a_scores = {a: val_score(coef_matrix(panel, "pair_shrunk", same_tr, cross_tr, direction, a))
                for a in alphas}
    alpha = max(a_scores, key=lambda k: a_scores[k])
    print(f"이벤트 |z|>={args.event_z} | train 추정 -> val 선택 -> test 보고")
    print(f"수축 비율 val 탐색: {({k: round(v, 4) for k, v in a_scores.items()})} -> alpha={alpha}")

    RULES = ["industry", "industry_signed", "industry_pair", "pair_corr", "pair_shrunk",
             "relation_score"]
    W_by_rule = {r: coef_matrix(panel, r, same_tr, cross_tr, direction, alpha) for r in RULES}

    # ── 제품이 실제로 묻는 것: "누가 **영향받나**" (부호 없는 순위)
    # 화면은 오를 종목/내릴 종목을 가르지 않고 "영향도가 큰 기업" 목록만 보여준다. 그러면
    # 순위 기준이 coef*x 가 아니라 |coef| 이고, 정답도 잔차가 아니라 |잔차| 다. 한 기사에서
    # x_A 는 모든 후보에 똑같이 곱해지는 스칼라라 순위를 바꾸지 못하므로, 이 목표에서는
    # **서빙 시점에 가격이 아예 필요 없다** (계수 추정에만 쓴다).
    for mkt in MARKETS:
        e = ev[mkt]
        e["truth_abs"] = np.abs(e["truth"])

    rows = []
    for uni, vkey, min_n in (("U1 전체 횡단면", "valid", 10), ("U2 그래프 이웃", "valid_nb", 3),
                             ("U3 이웃·부호없음", "valid_nb", 3)):
        print(f"\n── {uni} ──  (규칙마다 순위를 매길 수 있는 이벤트가 달라 "
              f"**공통 이벤트**에서 비교한다)")
        print(f"{'규칙':<18}{'시장':>8}{'val IC':>9}{'test IC':>9}{'t':>8}{'이벤트':>8}"
              f"{'vs industry':>13}{'t':>7}")
        unsigned = uni.startswith("U3")
        tkey = "truth_abs" if unsigned else "truth"
        for mkt in MARKETS:
            e = ev[mkt]
            common, sc_all = {}, {}
            for sp in ("val", "test"):
                idx = np.flatnonzero(e["split"] == sp)
                v = e[vkey][idx]
                keep = v.sum(1) >= min_n
                ranked = {}
                with np.errstate(invalid="ignore"):
                    for rule in RULES:
                        s = scores(W_by_rule[rule], mkt, idx)
                        if unsigned:
                            s = np.abs(s)
                        sc_all[(rule, sp)] = (idx, s)
                        z = np.where(v, s, np.nan)
                        ranked[rule] = (np.nanmax(z, axis=1) - np.nanmin(z, axis=1)) > 0
                # train_impact_gnn.py 와 같은 규칙: 공통 집합은 **커버리지가 넓은 규칙들**로만
                # 정한다. relation_score 는 소스의 일부만 덮어서(KOSPI 63% / NASDAQ 23%)
                # 교집합에 넣으면 비교 자체가 거기로 쪼그라들고, GNN 숫자와도 비교가 안 된다.
                broad = max(m.sum() for m in ranked.values()) * 0.8
                for m in ranked.values():
                    if m.sum() >= broad:
                        keep &= m
                common[sp] = keep
            per_event = {}
            for rule in RULES:
                r = {}
                for sp in ("val", "test"):
                    idx, s = sc_all[(rule, sp)]
                    k = common[sp]
                    r[sp] = event_ic(s[k], e[tkey][idx][k], e[vkey][idx][k], min_n=min_n)
                per_event[rule] = r["test"][3]
                # t 는 세션 클러스터로 낸다 — 같은 날 이벤트 수십 개는 독립이 아니다
                te_sess = e["sess"][np.flatnonzero(e["split"] == "test")][common["test"]]
                dm, _, dt, _ = paired_t(per_event[rule] - per_event["industry"], te_sess)
                d = np.array([dm])
                rows.append({"universe": uni, "rule": rule, "market": mkt,
                             "val_ic": r["val"][0], "test_ic": r["test"][0],
                             "test_t": r["test"][1], "n_events": r["test"][2],
                             "vs_industry": dm, "vs_industry_t": dt})
                print(f"{rule:<18}{mkt:>8}{r['val'][0]:>9.4f}{r['test'][0]:>9.4f}"
                      f"{r['test'][1]:>8.2f}{r['test'][2]:>8}{dm:>13.4f}{dt:>7.2f}")

    rep = pd.DataFrame(rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    rep.to_csv(args.report, index=False, encoding="utf-8")

    # 규칙은 **제품 목표와 같은 평가**의 val 로 고른다. 목표가 다르면 답도 다르다 —
    # 부호 없는 순위에서는 수축이 오히려 해롭다(같은 업종 안에서 변별력이 죽는다).
    sel_uni = "U3" if args.target == "unsigned" else "U1"
    sel = rep[rep.universe.str.startswith(sel_uni)]
    pick = sel.groupby("rule")["val_ic"].mean().idxmax()
    print(f"\n  목표={args.target} -> {sel_uni} 의 val 로 규칙 선택")
    print(f"\n  val 이 고른 규칙: **{pick}** (alpha={alpha})")
    for v in rep[rep.rule == pick].itertuples(index=False):
        print(f"    {v.universe:<14}{v.market:<8} val {v.val_ic:+.4f}  test {v.test_ic:+.4f}"
              f"  업종규칙 대비 {v.vs_industry:+.4f} (t={v.vs_industry_t:+.2f})")

    # 내보낼 표는 refit 구간으로 다시 추정한다
    fit = {"train"} if args.refit == "train" else {"train", "val"}
    same_f, cross_f = train_corr(panel, fit)
    Wf = coef_matrix(panel, pick, same_f, cross_f, direction, alpha)
    Cf = coef_matrix(panel, "pair_corr", same_f, cross_f, direction, alpha)
    n_days = int((np.isin(panel.split.to_numpy(), list(fit))).sum())

    out_rows = []
    for a in tk:
        for b in tk:
            c = float(Wf[pos[a], pos[b]])
            if a == b or abs(c) < 1e-6:
                continue
            same_ind = panel.industry.get(a, "NA") == panel.industry.get(b, "NA")
            rel_edge = b in neighbors.get(a, ())
            out_rows.append({
                "src_ticker": a, "dst_ticker": b,
                "src_market": panel.market[a], "dst_market": panel.market[b],
                "coefficient": round(c, 6),
                "impact_direction": "NEGATIVE" if c < 0 else "POSITIVE",
                # basis 는 "이 계수를 왜 믿는가"를 말한다. 관계 엣지인지와 같은 업종인지는
                # 서로 다른 근거라 한 칸에 뭉치면 안 된다 (처음엔 relation/industry 로만
                # 나눴는데, 그러면 "관계도 아니고 업종도 다른" 잡음 3.7만행이 industry 로
                # 라벨돼 버렸다).
                "basis": (("relation+" if rel_edge else "")
                          + ("same_industry" if same_ind else "cross_industry")),
                "src_industry": panel.industry.get(a, "NA"),
                "dst_industry": panel.industry.get(b, "NA"),
                "n_train_days": n_days,
                "pair_corr": round(float(Cf[pos[a], pos[b]]), 6),
                "rule": pick, "fit_window": args.refit,
            })

    def dump(path: Path, rows: list[dict], note: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        neg = sum(1 for r in rows if r["coefficient"] < 0)
        print(f"  {path.name:<34}{len(rows):>7}행  음수 {neg / max(len(rows), 1):>4.0%}  {note}")

    # 전체 조밀 행렬은 그대로 쓰면 안 된다 — 202x201 중 "관계도 아니고 업종도 다른" 쌍이
    # 37,394행이고 |계수| 중앙값이 0.024 로 사실상 잡음이다(시장 잔차 합이 0이라 생기는
    # 약한 음수). 제품에 넘길 것은 아래 둘로 나눈다.
    rel_rows = [r for r in out_rows if r["basis"].startswith("relation")]
    keep = [r for r in out_rows if abs(r["coefficient"]) >= args.min_abs_coef]
    print(f"\n  계수 표 (추정구간 {args.refit}, {n_days}세션, 규칙 {pick})")
    dump(args.out, out_rows, "전체 조밀 행렬 (진단용, 제품에 쓰지 말 것)")
    dump(args.out.with_name("impact_coefficients_relation.csv"), rel_rows,
         "관계 엣지만 — DB/화면용")
    dump(args.out.with_name("impact_coefficients_top.csv"), keep,
         f"|계수|>={args.min_abs_coef} — 확장 기능용")
    print(f"  -> {args.report}")


if __name__ == "__main__":
    main()
