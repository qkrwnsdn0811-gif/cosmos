"""서비스용 GNN: 단위 소스 -> 관계 이웃의 |시장 잔차 / train sigma| 순위(U3).

기존 U1 실험은 보존한다. 여기서는 소스 one-hot 값 1만 입력하므로 실시간 가격 없이
서빙할 수 있다. 뉴스 텍스트 자체를 학습하지 않으며 NER 가 찾은 기업을 입력받는다.
train 2017~2022, val 2023, test 2024~2026. 클리핑 경계/표 기준선은 train 이전 이력만
사용한다. val 로 체크포인트를 선택하고, 모든 시드 학습 후 test 를 보고한다.

손실은 관계 후보 쌍의 pairwise logistic loss 다. 소스가 같으면 항상 같은 logit 을 내므로,
train 이벤트의 쌍별 선호를 사전 집계해도 이벤트별 손실을 평균한 것과 같다. 각 이벤트의
가중치가 1이고 시장별 손실은 동일가중이다. GNN 은 매 업데이트 실제 메시지 전달을 학습한다.
동률 타깃 쌍은 손실에서 제외한다. IC 는 최소 3개 관계 후보가 있는 이벤트에서 계산하고,
모델 차이는 반드시 양쪽 IC 가 유효한 같은 이벤트를 세션별로 묶어 검정한다.

남는 한계: pre2024 공동언급과 first_date<2024 관계는 val 기간 정보를 포함한 고정 구조다.
업종/지분은 과거 as-of 가 없다. 따라서 완전한 시점별 백테스트라고 주장하지 않는다.
가격 기반 관계 부호/점수는 특징에서 제외한다. 미래 기사나 가격으로 만든 correlation
엣지는 사용하지 않는다. train sigma 가 없는 기업은 평가 타깃에서 제외하며, 이력 부족
기업의 서빙 결과는 구조 일반화일 뿐 검증된 cold-start 성능이 아니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

from impact_dataset import (HERE, INDUSTRY, PRICES, RELATIONS, REL_SCORED, SCORES,
                            build_edge_table, build_panel, coef_matrix, feature_cols,
                            load_relation_edges, train_corr)
from impact_ranker import infer_sources
from train_impact_gnn import ImpactGNN, MARKETS, aligned_tensors, event_ic, paired_t


def preference_counts(source, truth, valid, n_nodes):
    """각 이벤트에서 유효한 비교쌍의 총 가중치를 1로 맞춘 선호 집계."""
    pref = np.zeros((n_nodes, truth.shape[1], truth.shape[1]), dtype=np.float32)
    used = 0
    for node, values, mask in zip(source, truth, valid):
        cols = np.flatnonzero(mask)
        if len(cols) < 3:
            continue
        better = values[cols, None] > values[None, cols]
        pairs = int(better.sum())
        if pairs:
            pref[node][np.ix_(cols, cols)] += better.astype(np.float32) / pairs
            used += 1
    if not used:
        raise ValueError("순위 학습에 사용할 이벤트가 없습니다")
    return pref / used


def preference_loss(scores, preferences):
    differences = scores[:, :, None] - scores[:, None, :]
    return (F.softplus(-differences) * preferences).sum()


def make_events(panel, neighbors, event_z):
    aligned = aligned_tensors(panel)
    train = panel.resid.loc[panel.split == "train"]
    sd = train.std(ddof=0).to_numpy()
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, np.nan)
    events = {}
    for market, a in aligned.items():
        x = a["X"] / sd[None, :]
        y = np.abs(a["Y"] / sd[None, a["cols"]])
        session, source = np.nonzero(np.isfinite(x) & (np.abs(x) >= event_z))
        truth = y[session]
        valid = np.isfinite(truth) & neighbors[np.ix_(source, a["cols"])]
        valid &= source[:, None] != a["cols"][None, :]
        enough = valid.sum(1) >= 3
        events[market] = {"source": source[enough], "truth": truth[enough],
            "valid": valid[enough], "sess": session[enough], "cols": a["cols"],
            "split": panel.split.to_numpy()[a["rows"]][session[enough]]}
    return events


def evaluate(matrix, events, split):
    results = {}
    for market, e in events.items():
        take = e["split"] == split
        score = matrix[np.ix_(e["source"][take], e["cols"])]
        results[market] = event_ic(score, e["truth"][take], e["valid"][take], min_n=3)
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--hops", type=int, default=3)
    ap.add_argument("--lr", type=float, default=0.003)
    ap.add_argument("--weight-decay", type=float, default=0.0001)
    ap.add_argument("--event-z", type=float, default=2.0)
    ap.add_argument("--co-mention", type=Path, default=HERE / "data/edges_co_mention_pre2024.csv")
    ap.add_argument("--out-dir", type=Path, default=HERE / "artifacts/impact_u3")
    args = ap.parse_args()
    if min(args.epochs, args.patience, args.seeds, args.hops) < 1:
        ap.error("epochs/patience/seeds/hops 는 양수여야 합니다")
    if not args.co_mention.is_file():
        ap.error("평가용 pre2024 공동언급 파일이 필요합니다")
    if (args.out_dir / "model.pt").exists():
        ap.error("기존 모델을 덮어쓰지 않습니다. 새로운 --out-dir 을 지정하세요")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    panel = build_panel(beta_mode="prior", factors="market", winsor_fit_end="2022-12-31")
    edges = build_edge_table(panel, as_of="2024-01-01", co_mention_path=args.co_mention)
    feats = [c for c in feature_cols(edges) if c not in {"rel_sign", "rel_score", "rel_conf"}]
    tickers = panel.tickers
    pos = {t: i for i, t in enumerate(tickers)}
    n = len(tickers)
    neighbors = np.zeros((n, n), dtype=bool)
    relations = {}
    for r in load_relation_edges(as_of="2024-01-01").itertuples(index=False):
        if r.src in pos and r.dst in pos and r.src != r.dst:
            i, j = pos[r.src], pos[r.dst]
            neighbors[i, j] = True
            relations.setdefault(f"{i}:{j}", set()).add(r.rel_type)
    graph = {
        "src": torch.tensor([pos[t] for t in edges.src], dtype=torch.long, device=device),
        "dst": torch.tensor([pos[t] for t in edges.dst], dtype=torch.long, device=device),
        "features": torch.tensor(edges[feats].to_numpy(np.float32), device=device)}
    degree = np.bincount(graph["dst"].cpu().numpy(), minlength=n)
    graph["norm"] = torch.tensor(1 / np.sqrt(np.maximum(degree, 1)), dtype=torch.float32, device=device)
    events = make_events(panel, neighbors, args.event_z)
    preferences = {}
    for market, e in events.items():
        tr = e["split"] == "train"
        preferences[market] = torch.tensor(preference_counts(e["source"][tr], e["truth"][tr],
                                          e["valid"][tr], n), device=device)
        counts = {sp: int((e["split"] == sp).sum()) for sp in ("train", "val", "test")}
        if any(counts[sp] < 3 for sp in counts):
            raise ValueError(f"{market} 평가 이벤트 부족: {counts}")
        print(f"{market} U3 유효 이벤트 {counts}", flush=True)
    print(f"device={device} nodes={n} edges={len(edges)} features={len(feats)}", flush=True)
    config = {"n_feat": len(feats), "hops": args.hops}
    states, seed_rows, history = [], [], []
    unit_sources = torch.eye(n, device=device)
    target_cols = {m: torch.tensor(e["cols"], device=device) for m, e in events.items()}
    for seed in range(args.seeds):
        torch.manual_seed(seed)
        model = ImpactGNN(**config).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
        best, state, bad, best_epoch = -np.inf, None, 0, 0
        for epoch in range(1, args.epochs + 1):
            model.train()
            optimizer.zero_grad()
            scores = model(unit_sources, graph["src"], graph["dst"], graph["features"], graph["norm"])
            loss = torch.stack([preference_loss(scores[:, target_cols[m]], preferences[m])
                                for m in MARKETS]).mean()
            if not torch.isfinite(loss):
                raise RuntimeError(f"seed={seed} epoch={epoch}: loss 발산")
            loss.backward()
            grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            if not torch.isfinite(grad_norm):
                raise RuntimeError(f"seed={seed} epoch={epoch}: gradient 발산")
            optimizer.step()
            matrix = infer_sources(model, **graph, n_nodes=n)
            val = evaluate(matrix, events, "val")
            value = float(np.mean([val[m][0] for m in MARKETS]))
            if not np.isfinite(value):
                raise RuntimeError("유효한 val IC 를 계산할 수 없습니다")
            history.append({"seed": seed, "epoch": epoch, "loss": float(loss.detach()),
                            "val_kospi": val["KOSPI"][0], "val_nasdaq": val["NASDAQ"][0]})
            if value > best:
                best, bad, best_epoch = value, 0, epoch
                state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            else:
                bad += 1
            if epoch == 1 or epoch % 10 == 0:
                print(f"seed={seed} epoch={epoch} loss={float(loss.detach()):.5f} "
                      f"val K={val['KOSPI'][0]:+.4f} N={val['NASDAQ'][0]:+.4f} best={best:+.4f}", flush=True)
            if bad >= args.patience:
                break
        states.append(state)
        seed_rows.append({"seed": seed, "best_epoch": best_epoch, "val_mean_ic": best})
        print(f"seed={seed} 완료: val 선택 epoch={best_epoch}, mean IC={best:+.4f}", flush=True)

    # 모델 선택을 모두 마친 뒤 처음으로 test 를 평가한다.
    matrices = []
    for state in states:
        model = ImpactGNN(**config).to(device)
        model.load_state_dict(state)
        matrices.append(infer_sources(model, **graph, n_nodes=n))
    gnn = np.mean(matrices, axis=0)
    same, cross = train_corr(panel, {"train"})
    models = {"GNN": gnn, "uniform": np.ones((n, n))}
    for name in ("industry", "pair_corr", "pair_shrunk"):
        models[name] = np.abs(coef_matrix(panel, name, same, cross, {}, alpha=0.5))
    results = {name: {sp: evaluate(mat, events, sp) for sp in ("val", "test")}
               for name, mat in models.items()}
    report = []
    for market, e in events.items():
        sessions = e["sess"][e["split"] == "test"]
        for name in models:
            va, te = results[name]["val"][market], results[name]["test"][market]
            _, _, tc, ns = paired_t(te[3], sessions)
            report.append({"market": market, "model": name, "val_ic": va[0], "test_ic": te[0],
                "n_events": te[2], "eligible_events": len(sessions), "n_sessions": ns,
                "test_session_t": tc})
            print(f"{market} {name}: val={va[0]:+.4f} test={te[0]:+.4f} n={te[2]}/{len(sessions)}", flush=True)
        for base in ("industry", "pair_corr", "pair_shrunk"):
            a, b = results["GNN"]["test"][market][3], results[base]["test"][market][3]
            common = np.isfinite(a) & np.isfinite(b)
            dm, tn, tc, ns = paired_t(a - b, sessions)
            report.append({"market": market, "model": f"GNN - {base}", "test_ic": dm,
                "n_events": int(common.sum()), "n_sessions": ns, "test_session_t": tc,
                "test_naive_t": tn, "gnn_common_ic": float(a[common].mean()),
                "baseline_common_ic": float(b[common].mean())})
            print(f"{market} GNN - {base}: diff={dm:+.4f} session t={tc:+.2f} "
                  f"events={common.sum()} sessions={ns}", flush=True)
    paths = [PRICES, INDUSTRY, RELATIONS, REL_SCORED, SCORES, args.co_mention,
             HERE / "data/edges_sector.csv", HERE / "data/edges_ownership.csv"]
    metadata = {"created_at": datetime.now(timezone.utc).isoformat(), "graph_as_of": "2024-01-01",
        "price_end": "2026-09-11", "train": "2017-01-01/2022-12-31", "val": "2023",
        "test": "2024-01-01/2026-09-11", "winsor_fit_end": "2022-12-31",
        "input": "single_source_one_hot_value_1", "label": "absolute_market_residual_over_train_sigma",
        "selection": "equal_market_mean_val_rank_ic", "seed_results": seed_rows,
        "python": platform.python_version(), "torch": str(torch.__version__),
        "args": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        "input_sha256": {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "limitations": ["co_mention_and_relations_include_validation_period_structure",
                        "ownership_sector_have_no_historical_as_of", "not_news_text_conditioned",
                        "not_causal_or_next_session_forecast", "cold_start_not_validated",
                        "cuda_seeded_but_not_bitwise_deterministic", "market_specific_rank_scores"]}
    company = pd.read_csv(INDUSTRY, dtype=str).set_index("ticker")
    bundle = {"schema_version": 1, "objective": "U3_UNIT_SOURCE", "model_config": config,
        "states": states, "tickers": tickers, "markets": [panel.market[t] for t in tickers],
        "names": [company.at[t, "name_official"] for t in tickers], "feature_names": feats,
        "train_days": panel.resid.loc[panel.split == "train"].count().astype(int).tolist(),
        "neighbors": torch.tensor(neighbors), "relations": {k: sorted(v) for k, v in relations.items()},
        "metadata": metadata, **{k: v.detach().cpu() for k, v in graph.items()}}
    torch.save(bundle, args.out_dir / "model.pt")
    pd.DataFrame(report).to_csv(args.out_dir / "evaluation.csv", index=False)
    pd.DataFrame(history).to_csv(args.out_dir / "training_history.csv", index=False)
    (args.out_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    np.save(args.out_dir / "gnn_scores.npy", gnn)
    print(f"모델과 평가 저장: {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
