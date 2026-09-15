import type { SceneEdge, SceneModel } from "./graph";
import { industryGroup, RELATIONSHIP_META } from "./meta";

/**
 * 뜻밖의 관계 — "이 서비스로 생각지도 못한 관계를 알게 된다" 를 화면이 직접 하게 만드는 점수.
 * 그래프 스냅샷의 필드(유형·점수·양끝 기업의 산업·시장·연결 수·배치 좌표)만으로 계산하므로 추가 요청이 없다.
 * 사람이 "뜻밖" 이라고 느끼는 이유를 다섯 가지로 나눠 각각 0~1 로 재고, 가중합을 관계 점수로 살짝 눌러 정렬한다:
 *  - industry  두 기업의 산업이 다르다 (대분류까지 다르면 더)             — 자동차 ↔ 게임
 *  - market    상장 시장이 다르다                                          — KOSPI ↔ NASDAQ
 *  - frenemy   같은 두 기업 사이에 경쟁 관계와 협력·공급·투자 관계가 함께 있다 — 적이자 동료
 *  - quiet     둘 다 허브가 아니다 (연결 수 하위) — 허브를 따라가면 절대 못 보는 연결
 *  - rare      이 산업 조합·이 유형의 관계가 그래프에 이것 하나뿐이다
 *  - far       관계 점수 대비 은하에서 멀리 놓였다 — 각자 다른 무리에 속한다는 뜻
 * 이유 라벨은 UI(툴팁·패널·레일)가 그대로 보여 준다. 임계·가중치는 목업 300 간선 기준으로 상위 6 개가 실제로 "어?" 싶은 조합이 되게 맞췄다.
 */
export type SurpriseReasonKey = "industry" | "market" | "frenemy" | "quiet" | "rare" | "far";

export interface SurpriseReason {
  key: SurpriseReasonKey;
  /** 짧은 라벨 — 칩 */
  label: string;
  /** 한 줄 설명 — 툴팁·패널 */
  detail: string;
  /** 이 이유의 기여(0~1) × 가중치 */
  weight: number;
}

export interface Surprise {
  edgeId: string;
  /** 0~1. SURPRISE_MIN 이상이면 "뜻밖" 으로 표시한다 */
  score: number;
  reasons: SurpriseReason[];
}

/** 이 값 이상이면 뜻밖 표식(✦)을 붙인다 */
export const SURPRISE_MIN = 0.42;
/** 뜻밖 후보가 되기 위한 최소 관계 점수 — 약한 관계의 우연은 뜻밖이 아니라 잡음이다 */
const MIN_EDGE_SCORE = 55;

const W: Record<SurpriseReasonKey, number> = { industry: 0.3, market: 0.15, frenemy: 0.35, quiet: 0.22, rare: 0.15, far: 0.15 };

interface Ctx {
  /** 노드 id → 연결 수 백분위(0 = 가장 적음, 1 = 가장 많음) */
  degreePct: Map<string, number>;
  /** 정렬된 쌍 키 → 그 쌍에 있는 관계 유형들 */
  pairTypes: Map<string, Set<string>>;
  /** 산업 쌍 + 유형 키 → 그런 간선 수 */
  comboCount: Map<string, number>;
  /** 점수 구간(10점 단위) → 그 구간 간선들의 배치 거리 중앙값 */
  medianDist: Map<number, number>;
  /** 배치 거리의 전체 중앙값 — 구간 표본이 적을 때의 폴백 */
  medianAll: number;
}

const CTX = new WeakMap<SceneModel, Ctx>();

const pairKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);
const comboKey = (ia: string, ib: string, type: string) => (ia < ib ? `${ia}|${ib}|${type}` : `${ib}|${ia}|${type}`);
const dist = (e: SceneEdge) => Math.hypot(e.from[0] - e.to[0], e.from[1] - e.to[1], e.from[2] - e.to[2]);
const median = (xs: number[]) => {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

function contextOf(model: SceneModel): Ctx {
  const hit = CTX.get(model);
  if (hit) return hit;
  const degreePct = new Map<string, number>();
  const byDeg = [...model.nodes].sort((a, b) => a.degree - b.degree);
  byDeg.forEach((n, i) => degreePct.set(n.id, byDeg.length > 1 ? i / (byDeg.length - 1) : 1));

  const pairTypes = new Map<string, Set<string>>();
  const comboCount = new Map<string, number>();
  const distByBand = new Map<number, number[]>();
  const all: number[] = [];
  model.edges.forEach((e) => {
    const pk = pairKey(e.source, e.target);
    if (!pairTypes.has(pk)) pairTypes.set(pk, new Set());
    pairTypes.get(pk)!.add(e.type);
    const a = model.nodeById.get(e.source);
    const b = model.nodeById.get(e.target);
    if (a && b) {
      const ck = comboKey(a.industry, b.industry, e.type);
      comboCount.set(ck, (comboCount.get(ck) ?? 0) + 1);
    }
    const d = dist(e);
    all.push(d);
    const band = Math.floor(e.score / 10);
    if (!distByBand.has(band)) distByBand.set(band, []);
    distByBand.get(band)!.push(d);
  });
  const medianDist = new Map<number, number>();
  distByBand.forEach((xs, band) => {
    if (xs.length >= 4) medianDist.set(band, median(xs));
  });
  const ctx = { degreePct, pairTypes, comboCount, medianDist, medianAll: median(all) };
  CTX.set(model, ctx);
  return ctx;
}

/** 간선 하나의 뜻밖 점수와 이유. 관계 점수가 낮으면 이유가 있어도 0 이다 */
export function surpriseFor(model: SceneModel, edge: SceneEdge): Surprise {
  const none: Surprise = { edgeId: edge.id, score: 0, reasons: [] };
  if (edge.score < MIN_EDGE_SCORE) return none;
  const a = model.nodeById.get(edge.source);
  const b = model.nodeById.get(edge.target);
  if (!a || !b) return none;
  const ctx = contextOf(model);
  const reasons: SurpriseReason[] = [];
  const push = (key: SurpriseReasonKey, strength: number, label: string, detail: string) => {
    if (strength <= 0) return;
    reasons.push({ key, label, detail, weight: Math.min(1, strength) * W[key] });
  };

  // 산업 — 다르면 1, 대분류까지 다르면 1.5 배로 친다 (가중치 안에서 상한)
  if (a.industry && b.industry && a.industry !== b.industry) {
    const crossGroup = industryGroup(a.industry) !== industryGroup(b.industry);
    push("industry", crossGroup ? 1 : 0.65, "산업이 다름", `${a.industry} ↔ ${b.industry}`);
  }
  // 시장
  if (a.market && b.market && a.market !== b.market) push("market", 1, "시장이 다름", `${a.market} ↔ ${b.market}`);
  // 적이자 동료 — 같은 쌍에 경쟁과 다른 유형이 함께
  const types = ctx.pairTypes.get(pairKey(a.id, b.id));
  if (types && types.has("COMPETE") && types.size > 1) {
    const others = [...types].filter((t) => t !== "COMPETE").map((t) => RELATIONSHIP_META[t]?.label ?? t);
    push("frenemy", 1, "경쟁하면서도 " + others.join("·"), `같은 두 기업이 경쟁 관계와 ${others.join("·")} 관계를 함께 갖습니다`);
  }
  // 둘 다 허브가 아님 — 두 기업의 연결 수 백분위 중 큰 쪽이 0.5 아래일 때부터
  const pa = ctx.degreePct.get(a.id) ?? 1;
  const pb = ctx.degreePct.get(b.id) ?? 1;
  const quiet = (0.5 - Math.max(pa, pb)) / 0.5;
  if (quiet > 0) push("quiet", quiet, "둘 다 허브가 아님", `연결 ${a.degree}개 · ${b.degree}개 — 허브를 따라가면 보이지 않는 연결`);
  // 이 산업 조합·유형이 그래프에 하나뿐
  if ((ctx.comboCount.get(comboKey(a.industry, b.industry, edge.type)) ?? 0) <= 1 && a.industry !== b.industry) {
    push("rare", 1, "이런 조합은 하나뿐", `${a.industry}와 ${b.industry} 사이 ${RELATIONSHIP_META[edge.type]?.label ?? edge.type} 관계는 이것이 유일`);
  }
  // 점수 대비 멀리 — 같은 점수대 중앙값의 1.6 배부터 뜻밖, 2.6 배면 최대
  const ref = ctx.medianDist.get(Math.floor(edge.score / 10)) ?? ctx.medianAll;
  if (ref > 0) {
    const ratio = dist(edge) / ref;
    const far = (ratio - 1.6) / 1.0;
    if (far > 0) push("far", far, "다른 무리에 있음", "각자 다른 기업들과 더 가깝게 놓였는데도 이어져 있습니다");
  }

  if (!reasons.length) return none;
  reasons.sort((x, y) => y.weight - x.weight);
  const raw = reasons.reduce((s, r) => s + r.weight, 0);
  // 강한 관계일수록 같은 이유라도 더 놀랍다 (점수 55 → ×0.82, 100 → ×1.0)
  const score = Math.min(1, raw) * (0.6 + 0.4 * (edge.score / 100));
  return { edgeId: edge.id, score, reasons };
}

export interface RankOptions {
  limit?: number;
  /** 이 기업에 붙은 간선만 (기업 중심 뷰의 "이 기업의 뜻밖의 관계") */
  incidentTo?: string | null;
  /** 한 기업이 목록을 독차지하지 않게 — 기업당 최대 등장 수 */
  perCompany?: number;
}

/** 모델 전체(또는 한 기업 주변)에서 뜻밖 점수 상위 간선. 결과는 모델 객체에 캐시된다 */
const RANK = new WeakMap<SceneModel, Map<string, Surprise[]>>();
export function rankSurprises(model: SceneModel, opts: RankOptions = {}): Surprise[] {
  const { limit = 6, incidentTo = null, perCompany = 2 } = opts;
  const key = `${limit}|${incidentTo ?? ""}|${perCompany}`;
  let cache = RANK.get(model);
  if (!cache) {
    cache = new Map();
    RANK.set(model, cache);
  }
  const hit = cache.get(key);
  if (hit) return hit;

  const pool = incidentTo ? model.edges.filter((e) => e.source === incidentTo || e.target === incidentTo) : model.edges;
  const scored = pool
    .map((e) => surpriseFor(model, e))
    .filter((s) => s.score >= SURPRISE_MIN)
    .sort((x, y) => y.score - x.score);
  const seen = new Map<string, number>();
  const pairs = new Set<string>();
  const out: Surprise[] = [];
  for (const s of scored) {
    const e = model.edgeById.get(s.edgeId)!;
    // 같은 두 기업의 다른 유형(경쟁 + 공급)은 한 행으로 — 이유 칩("경쟁하면서도 공급")이 이미 그 사실을 말한다
    const pk = pairKey(e.source, e.target);
    if (pairs.has(pk)) continue;
    const ca = seen.get(e.source) ?? 0;
    const cb = seen.get(e.target) ?? 0;
    // 중심 기업이 있는 목록은 중심이 모든 행에 나오므로 상대 기업만 센다
    const overA = e.source !== incidentTo && ca >= perCompany;
    const overB = e.target !== incidentTo && cb >= perCompany;
    if (overA || overB) continue;
    seen.set(e.source, ca + 1);
    seen.set(e.target, cb + 1);
    pairs.add(pk);
    out.push(s);
    if (out.length >= limit) break;
  }
  cache.set(key, out);
  return out;
}

/** 씬(라벨·강조)이 빠르게 물을 수 있는 "뜻밖" 간선 id 집합 — 상위 목록보다 넓게, 임계를 넘는 전부 */
const SET = new WeakMap<SceneModel, Set<string>>();
export function surpriseSet(model: SceneModel): Set<string> {
  const hit = SET.get(model);
  if (hit) return hit;
  const set = new Set<string>();
  model.edges.forEach((e) => {
    if (surpriseFor(model, e).score >= SURPRISE_MIN) set.add(e.id);
  });
  SET.set(model, set);
  return set;
}
