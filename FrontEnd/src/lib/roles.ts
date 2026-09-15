import type { RelationshipType } from "@/api/types";
import type { SceneEdge, SceneModel } from "./graph";
import { relationshipMeta } from "./meta";

/**
 * 관계 역할 — "이 이웃은 나에게 무엇인가" 를 간선 유형 + 방향으로 풀어 쓴 것.
 * 간선 색은 주황 하나로 두고, 관계 유형 색(relationshipMeta.color)은 여기서 나온 역할을 통해
 * 노드 쪽 요소(이웃 링·칩 태그·툴팁·1홉 요약)에만 쓴다.
 */
export type RoleKey = "SUPPLIER" | "CUSTOMER" | "INVESTOR" | "INVESTEE" | "PARTNER" | "COMPETITOR" | "OTHER";

export interface RoleMeta {
  label: string;
  type: RelationshipType;
  /** in = 이웃 → 나 (공급사·투자자), out = 나 → 이웃 (고객·피투자), none = 무방향 */
  dir: "in" | "out" | "none";
}

export const ROLE_META: Record<RoleKey, RoleMeta> = {
  SUPPLIER: { label: "공급사", type: "SUPPLY", dir: "in" },
  CUSTOMER: { label: "고객", type: "SUPPLY", dir: "out" },
  INVESTOR: { label: "투자자", type: "INVEST", dir: "in" },
  INVESTEE: { label: "피투자", type: "INVEST", dir: "out" },
  PARTNER: { label: "협력", type: "PARTNER", dir: "none" },
  COMPETITOR: { label: "경쟁", type: "COMPETE", dir: "none" },
  OTHER: { label: "기타", type: "OTHER", dir: "none" },
};
/** 표시 순서 — RELATIONSHIP_ORDER(공급·투자·협력·경쟁)를 따르고 방향 있는 유형은 들어오는 쪽을 먼저 */
export const ROLE_ORDER: RoleKey[] = ["SUPPLIER", "CUSTOMER", "INVESTOR", "INVESTEE", "PARTNER", "COMPETITOR", "OTHER"];

export function roleColor(role: RoleKey) {
  return relationshipMeta(ROLE_META[role].type).color;
}

/** 간선 e 에서 selfId 의 상대가 맡는 역할. SUPPLY 의 source 는 공급하는 쪽이라 내가 source 면 상대는 고객이다 */
export function roleOf(e: SceneEdge, selfId: string): RoleKey {
  switch (e.type) {
    case "SUPPLY":
      return e.source === selfId ? "CUSTOMER" : "SUPPLIER";
    case "INVEST":
      return e.source === selfId ? "INVESTEE" : "INVESTOR";
    case "PARTNER":
      return "PARTNER";
    case "COMPETE":
      return "COMPETITOR";
    default:
      return "OTHER";
  }
}

/**
 * id 의 이웃별 대표 간선 — 같은 이웃과 간선이 여럿이면 점수가 가장 높은 하나만 남긴다.
 * allowed 가 있으면(필터) 통과한 간선만 본다.
 */
function bestEdgePerNeighbor(model: SceneModel, id: string, allowed?: (e: SceneEdge) => boolean) {
  const best = new Map<string, SceneEdge>();
  model.incident.get(id)?.forEach((eid) => {
    const e = model.edgeById.get(eid);
    if (!e || (allowed && !allowed(e))) return;
    const other = e.source === id ? e.target : e.source;
    const cur = best.get(other);
    if (!cur || e.score > cur.score) best.set(other, e);
  });
  return best;
}

/** 이웃 id → 역할 (호버 노드의 이웃 링·칩 태그가 쓴다) */
export function neighborRoles(model: SceneModel, id: string, allowed?: (e: SceneEdge) => boolean): Map<string, RoleKey> {
  const out = new Map<string, RoleKey>();
  bestEdgePerNeighbor(model, id, allowed).forEach((e, other) => out.set(other, roleOf(e, id)));
  return out;
}

export interface RoleCount {
  role: RoleKey;
  count: number;
  /** 대표 간선 점수 평균 */
  avg: number;
  /** 이 역할을 맡는 이웃 id */
  ids: string[];
}

/** 역할별 이웃 수·평균 점수 — ROLE_ORDER 순, 0 인 역할은 뺀다 (1홉 요약 스트립·노드 툴팁) */
export function roleCounts(model: SceneModel, id: string, allowed?: (e: SceneEdge) => boolean): RoleCount[] {
  const acc = new Map<RoleKey, { sum: number; ids: string[] }>();
  bestEdgePerNeighbor(model, id, allowed).forEach((e, other) => {
    const role = roleOf(e, id);
    const a = acc.get(role) ?? { sum: 0, ids: [] };
    a.sum += e.score;
    a.ids.push(other);
    acc.set(role, a);
  });
  return ROLE_ORDER.filter((r) => acc.has(r)).map((role) => {
    const a = acc.get(role)!;
    return { role, count: a.ids.length, avg: a.sum / a.ids.length, ids: a.ids };
  });
}
