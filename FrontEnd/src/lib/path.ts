import type { SceneEdge, SceneModel } from "./graph";

export interface PathResult {
  /** from 에서 to 까지의 노드 id (양끝 포함) */
  nodes: string[];
  /** nodes[i] 와 nodes[i+1] 를 잇는 간선 id */
  edges: string[];
  /** 경로 간선 중 가장 낮은 점수 — 병목 */
  bottleneck: number;
}

/** 호버 경로 게이트를 통과했을 때의 결과 — path 가 null 이면 '시도했지만 필터 때문에 이어지지 않음' 이다 */
export interface HoverPath {
  path: PathResult | null;
}

/**
 * 호버 노드까지의 경로를 계산할지 판단하고 계산까지 해 준다.
 * 기준점은 미리보기로 고른 기업(selectedId)이 있으면 그 기업, 없으면 기업 중심 뷰의 중심이고, 고른 기업이 없을 때는 depth ≥ 2 노드를
 * 호버해야 경로가 켜진다(depth 1 은 중심과 바로 이어져 기존 이웃 강조로 충분하다).
 * 3D 강조(Scene)와 HUD 스트립(PathStrip)이 같은 규칙을 두 벌 들고 있으면 한쪽만 고쳐 서로 다른 경로를 보여주기
 * 쉬워서, 게이트와 계산을 여기 한 곳에 모았다. 게이트를 통과하지 못하면 null 을 돌려준다.
 */
export function hoverPath(model: SceneModel | null, hoveredId: string | null, selectedId: string | null, allowed?: (e: SceneEdge) => boolean): HoverPath | null {
  if (!model || !hoveredId) return null;
  // 기준점: 미리보기로 고른 기업이 있으면 그 기업(은하 뷰), 없으면 기업 중심 뷰의 중심
  const base = selectedId ?? (model.kind === "system" ? model.centerId : null);
  if (!base || hoveredId === base) return null;
  const hovered = model.nodeById.get(hoveredId);
  if (!hovered || (!selectedId && hovered.depth < 2)) return null;
  return { path: shortestPath(model, base, hoveredId, allowed) };
}

/**
 * 두 기업 사이의 최단 경로 — 힙 없는 Dijkstra (노드 ≤ 200 이라 O(N²) 으로 충분).
 * 가중치는 `1 + 점수 벌점` 이고 벌점 합이 1 홉을 넘지 못하게 노드 수로 나눈다 — 홉 수가 먼저, 같은 홉 수면 점수 합이 높은 쪽이 이긴다.
 * (벌점을 그냥 0~1 로 두면 점수 높은 3홉이 점수 낮은 2홉을 이겨 '최소 홉' 이 깨진다)
 * allowed 는 최소 점수·관계 유형 필터만 넘긴다 — 간선 예산(상위만)은 무시해 숨어 있는 간선으로도 이어 준다.
 * 인접 간선을 직접 순회하므로 같은 두 노드 사이에 간선이 여럿이어도 필터를 통과한 최선의 것을 고른다.
 */
export function shortestPath(model: SceneModel, from: string, to: string, allowed?: (e: SceneEdge) => boolean): PathResult | null {
  if (from === to || !model.nodeById.has(from) || !model.nodeById.has(to)) return null;
  // 단순 경로는 간선이 최대 N-1 개라, 간선당 벌점을 1/(N+1) 아래로 두면 벌점 합이 1 홉보다 항상 작다
  const penaltyK = 100 * (model.nodes.length + 1);
  const dist = new Map<string, number>([[from, 0]]);
  const prevEdge = new Map<string, SceneEdge>();
  const done = new Set<string>();

  for (;;) {
    // 미방문 중 최단 거리 노드
    let u: string | null = null;
    let best = Infinity;
    dist.forEach((d, id) => {
      if (!done.has(id) && d < best) {
        best = d;
        u = id;
      }
    });
    if (u === null) return null;
    if (u === to) break;
    done.add(u);
    const uId: string = u;
    model.incident.get(uId)?.forEach((eid) => {
      const e = model.edgeById.get(eid);
      if (!e || (allowed && !allowed(e))) return;
      const v = e.source === uId ? e.target : e.source;
      if (done.has(v)) return;
      const nd = best + 1 + (100 - e.score) / penaltyK;
      if (nd < (dist.get(v) ?? Infinity)) {
        dist.set(v, nd);
        prevEdge.set(v, e);
      }
    });
  }

  // 역추적
  const nodes: string[] = [to];
  const edges: string[] = [];
  let bottleneck = 100;
  let cur = to;
  while (cur !== from) {
    const e = prevEdge.get(cur);
    if (!e) return null;
    edges.push(e.id);
    bottleneck = Math.min(bottleneck, e.score);
    cur = e.source === cur ? e.target : e.source;
    nodes.push(cur);
  }
  nodes.reverse();
  edges.reverse();
  return { nodes, edges, bottleneck };
}
