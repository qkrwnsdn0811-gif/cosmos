import type { SceneModel } from "@/lib/graph";
import type { PathResult } from "@/lib/path";
import { roleOf, type RoleKey } from "@/lib/roles";

export interface Emphasis {
  node: Map<string, number>;
  edge: Map<string, number>;
  /** 강조의 이유가 되는 노드 (라벨 항상 표시) */
  pinned: Set<string>;
  /**
   * 행성 정면에 로고 원판(배지)을 띄울 노드. 기본 상태에서는 행성 표면을 비워 툰 질감이 보이게 하고,
   * 호버 노드·중심 기업·선택(호버) 간선의 양끝에서만 배지가 떠오른다.
   */
  badges: Set<string>;
}

export interface EmphasisInput {
  hoveredId: string | null;
  hoveredEdgeId: string | null;
  selectedEdgeId: string | null;
  minScore: number;
  activeTypes: Set<string>;
  /**
   * 간선 예산: 기본 상태에서는 점수 상위 일부(+각 노드의 최강 간선)만 그리고,
   * 나머지는 노드를 호버했을 때만 나타난다. 관계가 많아도 화면이 읽히게 한다.
   */
  budget?: boolean;
  /** 경로 기준 → 호버 노드의 최단 경로 (Scene 이 계산). 있으면 호버 강조보다 우선한다 */
  path?: PathResult | null;
  /** 기업 중심 뷰 1홉 요약에서 고른 역할 — 중심의 그 역할 이웃·간선만 남긴다 (centerId 가 있을 때만 의미) */
  roleFilter?: RoleKey | null;
}

const DEPTH_DIM = [1, 1, 0.74, 0.56];
/** 뷰별 기본 표시 비율 — 은하는 핵심만(상위 8%), 기업 중심 뷰는 간선이 적으니 절반 이상 */
const BUDGET_RATIO: Record<SceneModel["kind"], number> = { galaxy: 0.08, system: 0.55 };
/** 각 노드의 가장 강한 간선 하나를 항상 남길지 — 기업 중심 뷰에서만. 은하에서는 노드 수만큼 간선이 깔려 화면을 덮는다 */
const KEEP_BEST_PER_NODE: Record<SceneModel["kind"], boolean> = { galaxy: false, system: true };

/** 필터를 통과한 간선 중 기본으로 그릴 것. 점수 상위 비율 (+ 기업 중심 뷰에서는 각 노드의 최강 간선) */
function budgetSet(model: SceneModel, visible: Set<string>, on: boolean) {
  if (!on) return visible;
  const arr = model.edges.filter((e) => visible.has(e.id));
  if (!arr.length) return visible;
  const scores = arr.map((e) => e.score).sort((a, b) => b - a);
  const cutoff = scores[Math.max(0, Math.ceil(scores.length * BUDGET_RATIO[model.kind]) - 1)];
  const keep = new Set<string>();
  const best = new Map<string, { id: string; score: number }>();
  arr.forEach((e) => {
    if (e.score >= cutoff) keep.add(e.id);
    for (const n of [e.source, e.target]) {
      const b = best.get(n);
      if (!b || e.score > b.score) best.set(n, { id: e.id, score: e.score });
    }
  });
  if (KEEP_BEST_PER_NODE[model.kind]) best.forEach((b) => keep.add(b.id));
  return keep;
}

/**
 * 어떤 노드·간선을 얼마나 밝게 그릴지 계산한다.
 * 우선순위: 경로 > 호버 노드 > 선택 간선 > 호버 간선 > 역할 필터 > 기업 중심 뷰의 depth 흐림 > 기본.
 * 필터(최소 점수·관계 유형)에 걸린 간선은 0 으로 숨기고, 간선 예산 밖의 간선은 호버 노드에 붙은 것만 보인다.
 */
export function computeEmphasis(model: SceneModel, input: EmphasisInput): Emphasis {
  const node = new Map<string, number>();
  const edge = new Map<string, number>();
  const pinned = new Set<string>();
  const badges = new Set<string>();

  const visible = new Set<string>();
  model.edges.forEach((e) => {
    const ok = e.score >= input.minScore && input.activeTypes.has(String(e.type));
    if (ok) visible.add(e.id);
  });
  const shown = budgetSet(model, visible, input.budget ?? false);

  const base = (id: string) => {
    const n = model.nodeById.get(id);
    return n ? DEPTH_DIM[Math.min(n.depth, 3)] : 1;
  };

  // 경로: 경로 위 노드·간선만 밝고, 호버 노드의 1홉 이웃은 중간 밝기로 남겨 다음 갈 곳이 보이게 한다.
  // 경로 간선은 예산과 무관하게 나타나야 하므로 visible 만 본다
  const path = input.path;
  if (path && path.nodes.length >= 2) {
    const onPath = new Set(path.nodes);
    const onEdges = new Set(path.edges);
    const nb = input.hoveredId ? (model.neighbors.get(input.hoveredId) ?? new Set<string>()) : new Set<string>();
    model.nodes.forEach((n) => node.set(n.id, onPath.has(n.id) ? 1 : nb.has(n.id) ? 0.45 : 0.12));
    model.edges.forEach((e) => edge.set(e.id, onEdges.has(e.id) ? (visible.has(e.id) ? 1 : 0) : shown.has(e.id) ? 0.05 : 0));
    onPath.forEach((id) => pinned.add(id));
    badges.add(path.nodes[0]);
    badges.add(path.nodes[path.nodes.length - 1]);
    return { node, edge, pinned, badges };
  }

  if (input.hoveredId && model.nodeById.has(input.hoveredId)) {
    const h = input.hoveredId;
    const nb = model.neighbors.get(h) ?? new Set();
    model.nodes.forEach((n) => node.set(n.id, n.id === h ? 1 : nb.has(n.id) ? 0.95 : 0.16));
    // 호버 노드의 간선은 예산과 무관하게 모두 나타난다
    model.edges.forEach((e) => edge.set(e.id, !visible.has(e.id) ? 0 : e.source === h || e.target === h ? 1 : shown.has(e.id) ? 0.08 : 0));
    pinned.add(h);
    nb.forEach((id) => pinned.add(id));
    badges.add(h);
    return { node, edge, pinned, badges };
  }

  const edgeFocus = input.selectedEdgeId ?? input.hoveredEdgeId;
  if (edgeFocus) {
    const sel = model.edgeById.get(edgeFocus);
    if (sel) {
      const strong = input.selectedEdgeId ? 0.14 : 0.35;
      model.nodes.forEach((n) => node.set(n.id, n.id === sel.source || n.id === sel.target ? 1 : strong));
      model.edges.forEach((e) => edge.set(e.id, !visible.has(e.id) ? 0 : e.id === sel.id ? 1 : shown.has(e.id) ? strong * 0.6 : 0));
      pinned.add(sel.source);
      pinned.add(sel.target);
      badges.add(sel.source);
      badges.add(sel.target);
      return { node, edge, pinned, badges };
    }
  }

  // 역할 필터: 중심의 인접 간선 중 그 역할인 것과 이웃만 남긴다. 예산은 무시하되 필터(visible)는 지킨다
  if (input.roleFilter && model.centerId && model.nodeById.has(model.centerId)) {
    const c = model.centerId;
    const keepEdges = new Set<string>();
    const keepNodes = new Set<string>([c]);
    model.incident.get(c)?.forEach((eid) => {
      const e = model.edgeById.get(eid);
      if (!e || !visible.has(e.id) || roleOf(e, c) !== input.roleFilter) return;
      keepEdges.add(e.id);
      keepNodes.add(e.source === c ? e.target : e.source);
    });
    model.nodes.forEach((n) => node.set(n.id, keepNodes.has(n.id) ? 1 : 0.2));
    model.edges.forEach((e) => edge.set(e.id, keepEdges.has(e.id) ? 1 : shown.has(e.id) ? 0.05 : 0));
    keepNodes.forEach((id) => pinned.add(id));
    badges.add(c);
    return { node, edge, pinned, badges };
  }

  model.nodes.forEach((n) => node.set(n.id, base(n.id)));
  model.edges.forEach((e) => edge.set(e.id, shown.has(e.id) ? Math.min(base(e.source), base(e.target)) : 0));
  if (model.centerId) {
    pinned.add(model.centerId);
    badges.add(model.centerId);
  }
  return { node, edge, pinned, badges };
}
