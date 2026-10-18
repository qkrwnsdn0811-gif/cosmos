import type { SceneModel } from "@/lib/graph";

type Shape = {
  centerId: SceneModel["centerId"];
  nodes: readonly { id: string }[];
  edges: readonly { id: string; source: string; target: string }[];
};

/**
 * 지금 그려지는 선(`visible` — 관계 종류·최소 점수 필터를 통과한 간선)이 하나도 없는 행성.
 * 은하는 이 행성들을 숨겨 관계가 있는 기업만 띄운다 — "함께 언급" 칩을 끄면 근거 관계가 있는 기업만 남는다.
 * 중심 기업과 `keep`(호버·고른 기업 등)은 선이 없어도 남긴다.
 */
export function isolatedNodes(model: Shape, visible: Set<string>, keep: readonly (string | null | undefined)[] = []): Set<string> {
  const linked = new Set<string>();
  for (const e of model.edges) {
    if (!visible.has(e.id)) continue;
    linked.add(e.source);
    linked.add(e.target);
  }
  const kept = new Set<string | null | undefined>([model.centerId, ...keep]);
  const out = new Set<string>();
  for (const n of model.nodes) if (!linked.has(n.id) && !kept.has(n.id)) out.add(n.id);
  return out;
}
