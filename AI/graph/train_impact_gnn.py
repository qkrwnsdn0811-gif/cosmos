"""Story 3 — 전체 횡단면(U1) 영향도 GNN 실험 재현.

소스 기업만 받는 U3는 train_impact_u3.py / impact_ranker.py / serve_impact.py.
원래 명세의 뉴스 입력 방향·강도 모델은 train_news_impact.py / serve_news_impact.py.
아래 실측과 제품 권고는 U3 주모델 채택 이전의 비교 기록이다.

  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py --hops 1
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py --edge-sets relation ownership sector
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py --permute 10

과제 정의 (단일 소스)
  이벤트 = 세션 t 에 종목 A 의 비정상 수익률이 크게 튄 것.
  입력   = **A 하나의 충격**. 그래프.
  출력   = 나머지 모든 종목 B 의 반응 점수.
  정답   = 같은 뉴스 사이클에서의 B 의 비정상 수익률.
  지표   = 이벤트별 횡단면 rank-IC (= "영향받은 종목 순위"의 품질).

다음날 예측이 아니다. event_study_propagation.py 에서 순전파가 0으로 나왔고 동시 동조만
남았다. 그러니 이건 예측이 아니라 귀속(attribution)이다 — 수익 신호로 읽으면 안 된다.

════════════════════════════════════════════════════════════════════════════
왜 "횡단면 전체를 입력으로" 쓰면 안 되는가 — 두 번 밟은 함정
════════════════════════════════════════════════════════════════════════════
처음 설계는 "B 의 이웃 전부의 당일 잔차로 B 를 맞힌다"였다. test rank-IC 0.47 이 나왔다.
금융에서 이 숫자가 나오면 모델이 좋은 게 아니라 새는 것이다.

1차 원인 — 산업 팩터. residualize() 는 그룹의 leave-one-out 평균을 빼므로 그룹 안에서 잔차
합이 0 에 묶인다. 산업 그룹별 평균 잔차 상관을 재면 경제가 아니라 1/(n-1) 이 보인다.
    그룹 크기      2~4개        5개         6개        9개       19~20개
    평균 잔차 상관  +0.13~+0.56  -0.09~-0.16 -0.04~-0.14 -0.02~-0.05 -0.034~-0.044
  MIN_GROUP=5 미만이라 산업 팩터를 아예 안 뺀 그룹만 양수다. 부호가 경제가 아니라
  "팩터를 뺐냐"로 갈린다. GNN 은 "같은 업종 k개면 -1/(k-1) 을 찍어라"를 배운 것이었다.

2차 원인 — 산업 팩터를 빼도 안 없어진다. 시장 팩터만 빼도 "전엣지 동일가중" 베이스라인이
KOSPI -0.14 였다. 베타가 대체로 1 근처라 **시장 전체에서도 잔차 합이 거의 0** 이기 때문이다.
합이 정확히 0이면 "나머지 전부의 합" = -(자기 자신)이라 상관이 -1 까지 간다. 횡단면을 통째로
입력에 넣는 한, 어떤 팩터 정의를 써도 모델은 이 항등식을 먼저 배운다.

  -> 그래서 **입력을 소스 하나로 제한한다.** 스칼라 하나로는 횡단면을 복원할 수 없다.
     남는 기계적 성분은 -1/99 수준이고 모든 B 에 거의 같은 크기로 실려 순위를 안 바꾼다.
     이게 제품이 실제로 묻는 것이기도 하다 — 화면은 "이 뉴스로 어디가 흔들렸나"를 보여준다.

부수 효과로 **다홉 전파가 안전해졌다.** B 의 값이 입력에 아예 없으므로 B -> A -> B 경로로
자기 값이 돌아올 일이 없다. 횡단면 입력이었다면 2층 GCN 은 구조적으로 샜다.
타깃 잔차는 시장 팩터만 뺀다 (--target-factors, 위 1차 원인 참고).

**엣지 미래정보**: correlation 엣지는 제외했다(test 구간 가격으로 만든 것이라 반칙).
co_mention 은 기본 파일이 2012~2026 누적이라 test 기사가 가중치에 들어간다 — 서버에서
`build_comention_edges.py --until 2024-01-01` 로 학습 구간 기사만 써서 다시 만든
`data/edges_co_mention_pre2024.csv` 를 `--co-mention` 으로 넘기면 깨끗하다. 7,383 -> 6,126쌍
(1,259쌍은 2024년 이후 기사로만 존재하던 엣지였다). 아래 결과는 전부 깨끗한 쪽이다.

════════════════════════════════════════════════════════════════════════════
실측 결과 — 룩업테이블을 포함한 같은 실행의 비교로 판단한다
════════════════════════════════════════════════════════════════════════════
재현 명령 (2026-09-14, 모델 설정 고정 후 test 보고)
  PYTHONUTF8=1 ../ner/.venv/Scripts/python.exe train_impact_gnn.py \
      --seeds 3 --epochs 60 --hops 3 --co-mention data/edges_co_mention_pre2024.csv
train 2017~2022 / val 2023 / test 2024-01-01~2026-09-11.
시장 잔차, 직전 연도 베타, 소스 |잔차|/train σ >= 2, correlation 엣지 제외.
GNN 은 시드 0/1/2 각각 val 로 체크포인트를 고른 뒤 **세 시드의 점수를 평균**해서 비교한다.
시드별 IC 의 평균과 이 앙상블의 IC 는 다르므로 둘을 혼용하지 않는다.

마무리 재실측 (2026-09-14, 공통 test KOSPI 9,486건 / NASDAQ 10,117건)
  모델                         KOSPI val  KOSPI test  NASDAQ val  NASDAQ test
  전엣지 동일가중                 0.0175     0.0164      0.0481      0.0597
  relation 부호x점수             0.0440     0.0415      0.0278      0.0190
  co_mention npmi              0.0681     0.0542      0.0442      0.0518
  산업 동일가중                  0.0927     0.0822      0.0604      0.0830
  pair_shrunk(표)              0.1072     0.0838      0.0801      0.1089
  GNN 3홉                     0.1081     0.0795      0.0720      0.0952
  relation 의 유효 test 이벤트는 KOSPI 6,663건 / NASDAQ 2,697건이다 (아래 2번).

짝지은 차이 (GNN - 기준선, 괄호는 세션 클러스터 t)
  기준선                 KOSPI (627세션)       NASDAQ (653세션)
  산업 동일가중            -0.0027 (-0.38)       +0.0122 (+2.33)
  pair_shrunk(표)         -0.0043 (-0.89)       -0.0138 (-5.04)
원본: data/impact_gnn_results.csv, 실행 로그: data/impact_gnn_story3_finish.log.
입력 SHA-256 과 라이브러리 버전: data/impact_gnn_story3_inputs.log.
직전 KOSPI 실행의 GNN val/test 0.1084/0.0804 와 이번 0.1081/0.0795 는 구분해 기록한다.
torch.manual_seed 는 고정하지만 CUDA 결정론 설정은 켜지 않았으므로 완전 동일 수치를
보장하지 않는다. 두 실행 모두 표 대비 평균이 낮았고 이번 실행의 차이 t 는 위와 같다.

읽는 법
1) KOSPI 에서는 GNN 의 val 평균 IC 가 표보다 조금 높았지만 test 평균은 낮았다.
   표와의 차이 t=-0.89 로 우열을 확정하지 않는다. NASDAQ 은 산업 기준선보다 높지만
   표보다 낮다(t=-5.04). 이번 설정에서 어느 시장도 GNN 의 표 대비 우위를 확인하지 못했다.
2) 공통 집합은 최대 커버리지의 80% 이상을 덮는 모델들로 정한다. relation 처럼 커버리지가
   좁은 모델은 교집합을 정할 때 제외하고 참고용으로 함께 출력한다. 따라서 relation 행은
   유효 이벤트 수가 더 적을 수 있으며, 그 평균을 다른 모델과 그대로 빼면 안 된다.
3) 일반 모델 행의 test_t 는 이벤트별 IC 로 계산한 순진 t 다. 검정에 사용할 것은
   `[공통] GNN - ...` 행의 test_t (세션 클러스터) 다. 이 차이 행의 n_events 는 **세션 수**다.
4) NASDAQ 전체 횡단면 head-to-head 와 제품용 관계 이웃(U2/U3) 평가는 다른 과제다.
   관계 이웃에서 pair_corr 의 우위를 확인하지 못했다는 결론을 전체 횡단면에 적용하지 않는다.
5) 옛 헤더의 2홉·재배선·전기간 공동언급 수치는 정정 전 부호와 다른 비교 집합이 섞여 있어
   현재 결과로 인용하지 않는다. 이번 재현 명령은 재배선 검정을 수행하지 않는다.

그래서 제품에는
  - **관계 이웃의 부호 없는 순위는 pair_corr 계수의 절댓값으로 낸다.** 별도 U3 평가에서
    KOSPI test IC 0.2054, 업종규칙 대비 짝지은 차이 +0.1317 (세션 t=+11.34) 였다.
    이 수치를 위 GNN 의 부호 있는 전체 횡단면 IC 와 직접 비교하지 않는다.
  - 제품용 파일은 build_impact_scores.py 의 impact_coefficients_relation.csv (568행) 다.
    관계 그래프는 후보와 관계 설명을 제공하고, 조밀 행렬 전체는 진단용으로만 쓴다.
  - DB impact_direction 은 유형별 혼합 (|r|>=0.05) 을 쓴다. market 잔차 test 적중률은
    유형 기본값 57.4%, 파라미터 0개인 전부 POSITIVE 63.5%, 채택 규칙 71.9% 다 (n=310).

"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from scipy import stats

from baseline_onehop import source_index
from impact_dataset import (HERE, Panel, build_edge_table, build_panel, coef_matrix,
                            feature_cols, train_corr)

DIRECTION_CSV = HERE / "data" / "db" / "relationship_impact_direction.csv"
OUT = HERE / "data" / "impact_gnn_results.csv"
MARKETS = ("KOSPI", "NASDAQ")


def aligned_tensors(panel: Panel):
    """타깃 시장별로 (소스 정렬 행렬 X, 타깃 행렬 Y, 세션 메타).

    X[i, j] = 타깃 세션 i 와 **같은 뉴스 사이클**에 속하는 종목 j 의 잔차.
    같은 시장이면 그 세션 자신. 교차시장이면 그 직전에 끝난 세션 — KRX 가 06:30 UTC 에,
    NASDAQ 이 20:00 UTC 에 닫으므로 KOSPI(D) 다음이 NASDAQ(D), 그 다음이 KOSPI(D+1) 이다.
    """
    R = panel.resid.to_numpy(dtype=float)
    days = panel.resid.index.values
    sess = panel.sess
    cols = {m: panel.cols(m) for m in MARKETS}
    sess_days = {m: days[sess[m]] for m in MARKETS}
    out = {}
    for tgt in MARKETS:
        t_rows = sess[tgt]
        X = np.full((len(t_rows), R.shape[1]), np.nan)
        for src in MARKETS:
            k = source_index(sess_days[tgt], sess_days[src], tgt, src, 0)
            ok = k >= 0
            srow = np.where(ok, sess[src][np.clip(k, 0, len(sess[src]) - 1)], 0)
            block = R[np.ix_(srow, cols[src])]
            block[~ok] = np.nan
            X[:, cols[src]] = block
        out[tgt] = {"X": X, "Y": R[np.ix_(t_rows, cols[tgt])], "rows": t_rows,
                    "days": days[t_rows], "cols": cols[tgt]}
    return out


def event_ic(scores: np.ndarray, truth: np.ndarray, valid: np.ndarray, min_n: int = 10):
    """이벤트별 횡단면 rank-IC 와 그 시계열 t.

    스피어만은 "순위에 대한 피어슨"이므로 행마다 rankdata 를 먹인 뒤 벡터화해서 잰다.
    이벤트마다 scipy.spearmanr 를 부르면 학습 중 검증에만 수십만 번 호출이라 GPU 가
    놀고 CPU 가 병목이 된다 (실제로 그렇게 짰다가 한참 기다렸다).
    """
    v = valid & np.isfinite(scores) & np.isfinite(truth)
    enough = v.sum(1) >= min_n
    if not enough.any():
        return float("nan"), float("nan"), 0, np.full(len(scores), np.nan)
    v = v[enough]
    rs = stats.rankdata(np.where(v, scores[enough], np.nan), axis=1, nan_policy="omit")
    ry = stats.rankdata(np.where(v, truth[enough], np.nan), axis=1, nan_policy="omit")
    rs, ry = np.where(v, rs, 0.0), np.where(v, ry, 0.0)
    n = v.sum(1, keepdims=True)
    sc = (rs - rs.sum(1, keepdims=True) / n) * v
    yc = (ry - ry.sum(1, keepdims=True) / n) * v
    den = np.sqrt((sc * sc).sum(1) * (yc * yc).sum(1))
    with np.errstate(invalid="ignore", divide="ignore"):
        ics = np.where(den > 0, (sc * yc).sum(1) / den, np.nan)
    out = np.full(len(scores), np.nan)          # 원래 행 위치를 유지한 이벤트별 IC
    out[np.flatnonzero(enough)] = ics
    ics = ics[np.isfinite(ics)]
    if len(ics) < 3:
        return float("nan"), float("nan"), len(ics), out
    se = ics.std(ddof=1) / math.sqrt(len(ics))
    return (float(ics.mean()), float(ics.mean() / se) if se > 0 else float("nan"),
            len(ics), out)


def paired_t(diff: np.ndarray, sess: np.ndarray) -> tuple[float, float, float, int]:
    """짝지은 IC 차이의 평균과 t. 순진한 t 와 **세션 클러스터** t 를 같이 낸다.

    같은 세션의 이벤트 수십 개는 독립 관측이 아니다 — 그날의 시장 상태를 공유한다.
    이벤트를 그대로 세면 표본이 실제보다 많은 셈이 돼 t 가 부풀려진다(실측 1.5~1.6배).
    세션 안에서 먼저 평균을 낸 뒤 세션끼리 t 를 구한다.
    """
    m = np.isfinite(diff)
    d, g = diff[m], sess[m]
    if len(d) < 3:
        return float("nan"), float("nan"), float("nan"), 0
    naive = d.mean() / (d.std(ddof=1) / math.sqrt(len(d))) if d.std(ddof=1) > 0 else np.nan
    order = np.argsort(g, kind="stable")
    d, g = d[order], g[order]
    cuts = np.flatnonzero(np.diff(g)) + 1
    means = np.array([b.mean() for b in np.split(d, cuts)])
    clus = (means.mean() / (means.std(ddof=1) / math.sqrt(len(means)))
            if len(means) > 2 and means.std(ddof=1) > 0 else float("nan"))
    return float(d.mean()), float(naive), float(clus), len(means)


class ImpactGNN(torch.nn.Module):
    """소스 충격 하나를 그래프로 전파한다. 홉마다 전달계수를 따로 학습한다.

    메시지 = gain(엣지특징, |들어온 신호|) x 들어온 신호.
    gain = a(e) + Σ_k c_k(e)·ψ_k(|h|) 로 인수분해했다 — (batch, edge, feature) 텐서를
    만들지 않으려는 것이고, ψ 가 학습되는 기저라 |h| 에 대한 비선형성은 유지된다.
    최종 점수는 홉별 결과의 학습된 가중합이라 1홉이 최적이면 모델이 스스로 그렇게 수렴한다.
    """

    def __init__(self, n_feat: int, hops: int = 2, n_basis: int = 4, hidden: int = 32):
        super().__init__()
        self.hops = hops
        self.edge = torch.nn.ModuleList(
            torch.nn.Sequential(torch.nn.Linear(n_feat, hidden), torch.nn.ReLU(),
                                torch.nn.Linear(hidden, hidden), torch.nn.ReLU(),
                                torch.nn.Linear(hidden, n_basis + 1))
            for _ in range(hops))
        self.basis = torch.nn.ModuleList(
            torch.nn.Sequential(torch.nn.Linear(1, 16), torch.nn.Tanh(),
                                torch.nn.Linear(16, n_basis))
            for _ in range(hops))
        self.mix = torch.nn.Parameter(torch.tensor([1.0] + [0.3] * (hops - 1)))

    def forward(self, h0: torch.Tensor, src: torch.Tensor, dst: torch.Tensor,
                efeat: torch.Tensor, norm: torch.Tensor) -> torch.Tensor:
        """norm = 1/sqrt(입력차수). 안 나누면 2홉에서 크기가 제곱으로 튀어 학습이 발산한다
        (실제로 NaN 이 났다). 노드마다 들어오는 엣지 수가 4개~150개로 크게 다르다."""
        h, total = h0, 0.0
        for l in range(self.hops):
            xs = h[:, src]
            coef = self.edge[l](efeat)                       # (E, K+1)
            psi = self.basis[l](xs.abs().clamp(max=20.0).unsqueeze(-1))     # (B, E, K)
            gain = coef[:, 0] + (psi * coef[None, :, 1:]).sum(-1)
            h = torch.zeros_like(h0).index_add_(1, dst, gain * xs) * norm
            total = total + self.mix[l] * h
        return total


def listwise_loss(scores: torch.Tensor, truth: torch.Tensor,
                  valid: torch.Tensor) -> torch.Tensor:
    """이벤트별 횡단면 피어슨 상관의 음수. 지표(rank-IC)와 같은 것을 직접 최적화한다.

    분산이 0인 이벤트를 반드시 빼야 한다. 소스에 엣지가 하나도 없으면 점수가 전부 0이 되고,
    그러면 sqrt(0) 의 미분이 무한대라 **손실은 멀쩡한데 기울기만 NaN** 이 된다. 첫 스텝부터
    전 파라미터가 죽는데 손실값만 봐서는 안 보인다. clamp 는 sqrt 안에서 걸어야 한다.
    """
    n = valid.sum(1, keepdim=True).clamp_min(1.0)
    s, y = scores * valid, truth * valid
    sc = (s - s.sum(1, keepdim=True) / n) * valid
    yc = (y - y.sum(1, keepdim=True) / n) * valid
    vs, vy = (sc * sc).sum(1), (yc * yc).sum(1)
    ok = (valid.sum(1) >= 10) & (vs > 1e-10) & (vy > 1e-10)
    if not ok.any():
        return scores.sum() * 0.0
    den = torch.sqrt((vs * vy).clamp_min(1e-12))
    return -((sc * yc).sum(1) / den)[ok].mean()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge-sets", nargs="*",
                    default=["relation", "ownership", "sector", "co_mention"])
    ap.add_argument("--as-of", default="2024-01-01", help="관계 엣지 first_date 컷")
    ap.add_argument("--co-mention", default=None, type=Path,
                    help="공동언급 엣지 파일. 기본 edges_co_mention.csv 는 2012~2026 누적이라 "
                         "test 기사가 가중치에 들어간다. edges_co_mention_pre2024.csv 를 주면 "
                         "학습 구간 기사만으로 만든 깨끗한 엣지를 쓴다")
    ap.add_argument("--target-factors", choices=("market", "market+industry", "none"),
                    default="market", help="타깃 잔차에서 뺄 팩터 (docstring 1차 원인 참고)")
    ap.add_argument("--event-z", type=float, default=2.0,
                    help="소스 |잔차|/σ 가 이 값 이상인 것을 이벤트로 본다")
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--no-industry-features", action="store_true",
                    help="엣지 특징에서 산업 원핫을 뺀다 (기여분 확인용)")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--lr", type=float, default=3e-3)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--patience", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--alpha", type=float, default=0.5,
                    help="pair_shrunk 수축 비율. build_impact_scores.py 가 val 로 0.5 를 골랐다")
    ap.add_argument("--permute", type=int, default=0,
                    help="그래프를 섞고 똑같이 학습시킨 귀무분포 반복 횟수")
    ap.add_argument("--out", default=OUT, type=Path)
    args = ap.parse_args()

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    panel = build_panel(beta_mode="prior", factors=args.target_factors)
    edges = build_edge_table(panel, as_of=args.as_of, sets=tuple(args.edge_sets),
                             direction_csv=DIRECTION_CSV, co_mention_path=args.co_mention)
    A = aligned_tensors(panel)
    pos = {t: i for i, t in enumerate(panel.tickers)}
    n_nodes = len(panel.tickers)

    # 학습 구간 표준편차로만 정규화 (전 구간 std 는 그것도 미래정보다)
    tr_rows = panel.split.to_numpy() == "train"
    sd = np.nanstd(panel.resid.to_numpy()[tr_rows], axis=0)
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, 1.0)

    src_g = torch.tensor([pos[s] for s in edges["src"]], dtype=torch.long, device=dev)
    dst_g = torch.tensor([pos[t] for t in edges["dst"]], dtype=torch.long, device=dev)
    FEATS = feature_cols(edges, industry=not args.no_industry_features)
    efeat = torch.tensor(edges[FEATS].to_numpy(np.float32), device=dev)
    indeg = np.bincount(dst_g.cpu().numpy(), minlength=n_nodes).astype(np.float32)
    norm_g = torch.tensor(1.0 / np.sqrt(np.maximum(indeg, 1.0)), device=dev)

    # ---- 이벤트 만들기: (타깃시장, 세션, 소스종목)
    ev = {}
    for mkt in MARKETS:
        d = A[mkt]
        Xz = d["X"] / sd[None, :]
        Yz = d["Y"] / sd[None, d["cols"]]
        split = panel.split.to_numpy()[d["rows"]]
        i_idx, j_idx = np.nonzero(np.isfinite(Xz) & (np.abs(Xz) >= args.event_z))
        keep = np.isin(split[i_idx], ["train", "val", "test"])
        i_idx, j_idx = i_idx[keep], j_idx[keep]
        h0 = np.zeros((len(i_idx), n_nodes), dtype=np.float32)
        h0[np.arange(len(i_idx)), j_idx] = Xz[i_idx, j_idx]
        truth = np.nan_to_num(Yz[i_idx])
        valid = np.isfinite(Yz[i_idx])
        # 소스 자신은 채점에서 뺀다 — 자기 뉴스에 자기가 반응한 건 영향도가 아니다
        local = {c: k for k, c in enumerate(d["cols"])}
        for r, j in enumerate(j_idx):
            if j in local:
                valid[r, local[j]] = False
        # 손실용 타깃은 이벤트별 순위로 바꾼다. 지표가 rank-IC(스피어만)이므로 순위에 대한
        # 피어슨을 최적화하는 것이 곧 지표를 직접 최적화하는 것이다. 값 그대로 쓰면 꼬리
        # 몇 개가 손실을 지배해 "큰 놈 하나 맞히기"로 샌다.
        rank = np.zeros_like(truth)
        for r in range(len(truth)):
            m = valid[r]
            if m.sum() >= 2:
                o = stats.rankdata(truth[r][m])
                rank[r][m] = (o - o.mean()) / max(o.std(), 1e-9)
        ev[mkt] = {"h0": torch.tensor(h0, device=dev),
                   "truth": torch.tensor(rank.astype(np.float32), device=dev),
                   "valid": torch.tensor(valid.astype(np.float32), device=dev),
                   "truth_np": truth, "valid_np": valid, "split": split[i_idx],
                   "cols": d["cols"], "src_node": j_idx, "sess": i_idx}

    tgt_cols = {m: torch.tensor(A[m]["cols"], dtype=torch.long, device=dev) for m in MARKETS}

    print(f"장치 {dev} | 엣지 {len(edges)} 특징 {len(FEATS)}개 ({args.edge_sets}) "
          f"| 타깃팩터 {args.target_factors} "
          f"| 홉 {args.hops} | 이벤트 |z|>={args.event_z}")
    for m in MARKETS:
        c = pd.Series(ev[m]["split"]).value_counts()
        print(f"  {m:<7} 이벤트 train/val/test = {c.get('train', 0)}/{c.get('val', 0)}"
              f"/{c.get('test', 0)}  (타깃 {len(ev[m]['cols'])}종목)")

    def score_fixed(mkt: str, w: np.ndarray, idx: np.ndarray) -> np.ndarray:
        """학습 없는 한 홉 전파. GNN 과 같은 이벤트·같은 마스크로 잰다."""
        W = np.zeros((n_nodes, n_nodes))
        np.add.at(W, (src_g.cpu().numpy(), dst_g.cpu().numpy()), w)
        return ev[mkt]["h0"].cpu().numpy()[idx] @ W[:, A[mkt]["cols"]]

    # build_impact_scores.py 의 룩업테이블 규칙을 **같은 실행 안에서** 맞붙인다. 두 스크립트를
    # 따로 돌리면 공통 이벤트 집합이 달라져 숫자를 나란히 못 놓는다.
    direction = {}
    if DIRECTION_CSV.exists():
        for r in pd.read_csv(DIRECTION_CSV, dtype=str).itertuples(index=False):
            direction[(r.source_stock_code, r.target_stock_code)] = r.impact_direction
    same_tr, cross_tr = train_corr(panel, {"train"})
    DENSE = {"industry": coef_matrix(panel, "industry", same_tr, cross_tr, direction),
             "pair_shrunk": coef_matrix(panel, "pair_shrunk", same_tr, cross_tr,
                                        direction, args.alpha)}

    def score_dense(mkt: str, W: np.ndarray, idx: np.ndarray) -> np.ndarray:
        e = ev[mkt]
        return W[np.ix_(e["src_node"][idx], A[mkt]["cols"])] * (
            e["h0"][idx].cpu().numpy()[np.arange(len(idx)), e["src_node"][idx]][:, None])

    rows = []
    # 모델마다 점수를 낼 수 있는 이벤트 수가 다르다 — 소스에 그 종류의 엣지가 없으면 점수가
    # 전부 0이라 순위가 안 생긴다. 그대로 비교하면 커버리지가 좁은 모델이 쉬운 이벤트만 골라
    # 푼 셈이 된다 (산업 베이스라인 10,994 vs GNN 11,663 으로 5.7% 차이가 실제로 났다).
    # 그래서 점수를 다 모아두고 **모든 모델이 순위를 매길 수 있는 공통 이벤트**에서 다시 잰다.
    scored: dict[tuple[str, str], dict[str, np.ndarray]] = {
        (m, sp): {} for m in MARKETS for sp in ("val", "test")}
    print(f"\n{'모델':<24}{'시장':>8}{'val IC':>9}{'test IC':>9}{'t':>8}{'이벤트':>8}")
    # 가장 강한 "학습 없는" 조합도 같이 둔다. 섞는 비율만 val 에서 고른다 — GNN 이
    # 이걸 못 넘으면 202노드 그래프에서 학습으로 더 얻을 게 없다는 뜻이다.
    best_mix, best_v = 0.0, -9.0
    for a in np.linspace(0, 1, 11):
        w = (a * edges["sector"] + (1 - a) * edges["com_npmi"]).to_numpy()
        v = np.mean([event_ic(score_fixed(m, w, np.flatnonzero(ev[m]["split"] == "val")),
                              ev[m]["truth_np"][np.flatnonzero(ev[m]["split"] == "val")],
                              ev[m]["valid_np"][np.flatnonzero(ev[m]["split"] == "val")])[0]
                     for m in MARKETS])
        if np.isfinite(v) and v > best_v:
            best_mix, best_v = float(a), v

    for name in ("co_mention npmi", "relation 부호x점수", "전엣지 동일가중", "산업 동일가중",
                 f"산업x{best_mix:.1f}+공동언급", "pair_shrunk(표)"):
        for mkt in MARKETS:
            if name.startswith("co_mention"):
                w = edges["com_npmi"].to_numpy()
            elif name.startswith("relation"):
                w = (edges["rel_sign"] * np.maximum(edges["rel_score"], 0.05)
                     * edges["has_rel"]).to_numpy()
            elif name.startswith("전엣지"):
                w = np.ones(len(edges))
            elif name.startswith("산업x"):
                w = (best_mix * edges["sector"]
                     + (1 - best_mix) * edges["com_npmi"]).to_numpy()
            else:
                w = edges["sector"].to_numpy()
            r = {}
            for sp in ("val", "test"):
                idx = np.flatnonzero(ev[mkt]["split"] == sp)
                sc = (score_dense(mkt, DENSE["pair_shrunk"], idx)
                      if name.startswith("pair_shrunk") else score_fixed(mkt, w, idx))
                scored[(mkt, sp)][name] = sc
                r[sp] = event_ic(sc, ev[mkt]["truth_np"][idx], ev[mkt]["valid_np"][idx])
            rows.append({"model": name, "market": mkt, "val_ic": r["val"][0],
                         "test_ic": r["test"][0], "test_t": r["test"][1],
                         "n_events": r["test"][2]})
            print(f"{name:<24}{mkt:>8}{r['val'][0]:>9.4f}{r['test'][0]:>9.4f}"
                  f"{r['test'][1]:>8.2f}{r['test'][2]:>8}")

    def train_one(mkt: str, seed: int, dst_override: torch.Tensor | None = None):
        e, dst = ev[mkt], (dst_g if dst_override is None else dst_override)
        if dst_override is None:
            norm = norm_g
        else:   # 재배선하면 입력차수도 바뀌므로 정규화를 다시 만든다
            d = np.bincount(dst.cpu().numpy(), minlength=n_nodes).astype(np.float32)
            norm = torch.tensor(1.0 / np.sqrt(np.maximum(d, 1.0)), device=dev)
        tr = np.flatnonzero(e["split"] == "train")
        va = np.flatnonzero(e["split"] == "val")
        te = np.flatnonzero(e["split"] == "test")
        torch.manual_seed(seed)
        model = ImpactGNN(len(FEATS), hops=args.hops).to(dev)
        opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
        best_val, best_state, bad = -9.0, None, 0
        g = torch.Generator().manual_seed(seed)

        def infer(idx: np.ndarray) -> np.ndarray:
            model.eval()
            outs = []
            with torch.no_grad():
                for i in range(0, len(idx), 512):
                    b = idx[i:i + 512]
                    outs.append(model(e["h0"][b], src_g, dst, efeat, norm)[:, tgt_cols[mkt]].cpu())
            return torch.cat(outs).numpy() if outs else np.zeros((0, len(e["cols"])))

        for _ in range(args.epochs):
            model.train()
            perm = torch.randperm(len(tr), generator=g).numpy()
            for i in range(0, len(perm), args.batch):
                b = tr[perm[i:i + args.batch]]
                opt.zero_grad()
                out = model(e["h0"][b], src_g, dst, efeat, norm)[:, tgt_cols[mkt]]
                loss = listwise_loss(out, e["truth"][b], e["valid"][b])
                if not torch.isfinite(loss):
                    continue
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
            v = event_ic(infer(va), e["truth_np"][va], e["valid_np"][va])[0]
            if np.isfinite(v) and v > best_val:
                best_val, bad = v, 0
                best_state = {k: t.detach().clone() for k, t in model.state_dict().items()}
            else:
                bad += 1
                if bad >= args.patience:
                    break
        if best_state is None:
            raise RuntimeError(f"{mkt} seed={seed}: val IC 가 한 번도 유한하지 않았다 "
                               "— 학습이 발산했을 가능성이 높다")
        model.load_state_dict(best_state)
        sc_te, sc_va = infer(te), infer(va)
        return (best_val, event_ic(sc_te, e["truth_np"][te], e["valid_np"][te]),
                {"val": sc_va, "test": sc_te})

    gnn = {}
    for mkt in MARKETS:
        res = [train_one(mkt, s) for s in range(args.seeds)]
        for sp in ("val", "test"):      # 시드 평균 점수를 GNN 대표 점수로 쓴다
            scored[(mkt, sp)]["GNN"] = np.mean([r[2][sp] for r in res], axis=0)
        for s, (bv, te, _) in enumerate(res):
            print(f"{'GNN seed' + str(s):<24}{mkt:>8}{bv:>9.4f}{te[0]:>9.4f}"
                  f"{te[1]:>8.2f}{te[2]:>8}")
        ts = np.array([r[1][0] for r in res])
        gnn[mkt] = ts
        rows.append({"model": f"GNN {args.hops}홉 (평균)", "market": mkt,
                     "val_ic": float(np.mean([r[0] for r in res])), "test_ic": ts.mean(),
                     "test_t": res[0][1][1], "n_events": res[0][1][2]})
        sd_txt = f"±{ts.std(ddof=1):.4f}" if len(ts) > 1 else ""
        print(f"{'GNN 평균(' + str(args.seeds) + '시드)':<24}{mkt:>8}"
              f"{np.mean([r[0] for r in res]):>9.4f}{ts.mean():>9.4f}{sd_txt:>8}")

    if args.permute:
        print(f"\n  재배선 귀무분포 {args.permute}회 (dst 를 섞고 학습 절차는 그대로)")
        rng = np.random.default_rng(20260914)
        for mkt in MARKETS:
            null = [train_one(mkt, i, dst_override=torch.tensor(
                rng.permutation(dst_g.cpu().numpy()), dtype=torch.long, device=dev))[1][0]
                for i in range(args.permute)]
            a = np.array([v for v in null if np.isfinite(v)])
            real = gnn[mkt].mean()
            z = (real - a.mean()) / a.std(ddof=1) if len(a) > 1 and a.std(ddof=1) > 0 else np.nan
            rows.append({"model": "GNN 재배선(귀무)", "market": mkt, "val_ic": float("nan"),
                         "test_ic": a.mean(), "test_t": z, "n_events": len(a)})
            print(f"    {mkt:<8} 실제 {real:+.4f} | 귀무 {a.mean():+.4f}"
                  f"±{a.std(ddof=1):.4f} | z={z:+.1f}")

    print("\n  ── 공통 이벤트에서만 다시 비교 (모든 모델이 순위를 매길 수 있는 것) ──")
    print(f"{'모델':<24}{'시장':>8}{'val IC':>9}{'test IC':>9}{'t':>8}{'이벤트':>8}")
    for mkt in MARKETS:
        common = {}
        for sp in ("val", "test"):
            idx = np.flatnonzero(ev[mkt]["split"] == sp)
            v = ev[mkt]["valid_np"][idx]
            keep = v.sum(1) >= 10
            with np.errstate(invalid="ignore"):
                ranked = {}
                for nm, sc in scored[(mkt, sp)].items():
                    z = np.where(v, sc, np.nan)      # 무효 타깃은 빼고 점수 변동을 본다
                    ranked[nm] = (np.nanmax(z, axis=1) - np.nanmin(z, axis=1)) > 0
            # 공통 집합은 **커버리지가 넓은 모델들**로만 정한다. relation 엣지는 소스의 63%
            # (NASDAQ 은 23%)밖에 못 덮어서, 얘까지 교집합에 넣으면 비교 자체가 relation
            # 커버리지로 쪼그라든다. 좁은 모델은 이 집합 위에서 점수만 같이 보고한다.
            broad = max(m.sum() for m in ranked.values()) * 0.8
            for nm, m in ranked.items():
                if m.sum() >= broad:
                    keep &= m
            common[sp] = (idx, keep)
        per_event = {}
        for name in scored[(mkt, "test")]:
            r = {}
            for sp in ("val", "test"):
                idx, keep = common[sp]
                r[sp] = event_ic(scored[(mkt, sp)][name][keep],
                                 ev[mkt]["truth_np"][idx][keep],
                                 ev[mkt]["valid_np"][idx][keep])
            per_event[name] = r["test"][3]
            rows.append({"model": f"[공통] {name}", "market": mkt, "val_ic": r["val"][0],
                         "test_ic": r["test"][0], "test_t": r["test"][1],
                         "n_events": r["test"][2]})
            print(f"{name:<24}{mkt:>8}{r['val'][0]:>9.4f}{r['test'][0]:>9.4f}"
                  f"{r['test'][1]:>8.2f}{r['test'][2]:>8}")

        # GNN 이 베이스라인을 "이겼다"고 말하려면 각자의 t 값이 아니라 **차이**를 검정해야
        # 한다. 같은 이벤트에서 두 IC 를 짝지어 빼고 그 평균이 0인지 본다.
        te_idx, te_keep = common["test"]        # common[sp] 는 (행 인덱스, 불리언 마스크)
        te_sess = ev[mkt]["sess"][te_idx][te_keep]
        for base in ("산업 동일가중", "pair_shrunk(표)"):
            if "GNN" not in per_event or base not in per_event:
                continue
            m, tn, tc, ns = paired_t(per_event["GNN"] - per_event[base], te_sess)
            rows.append({"model": f"[공통] GNN - {base}", "market": mkt,
                         "val_ic": float("nan"), "test_ic": m, "test_t": tc,
                         "n_events": ns})
            print(f"{('  GNN - ' + base):<24}{mkt:>8}{'':>9}{m:>9.4f}"
                  f"{tc:>8.2f}{ns:>8}   (순진 t={tn:+.2f}, 세션 {ns}일)")

    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8")
    print(f"\n  -> {args.out}")


if __name__ == "__main__":
    main()
