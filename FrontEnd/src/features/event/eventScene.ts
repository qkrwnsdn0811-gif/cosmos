import type { CompanyGraph, GraphEdge } from "@/api/types";

/**
 * 이벤트(기사 1건) → 3D 태양계 배치 입력. 기사가 가운데 별(depth 0)이고, 기사에 나온 기업이 안쪽 궤도(1),
 * 공급망으로 추정한 기업이 1단계면 2, 2단계면 3 궤도에 앉는다 — 은하의 기업 중심 배치(layoutSystem)를 그대로 쓴다.
 * 선은 기사 → 언급 기업(MENTION)과, 지도 위 두 기업을 잇는 전이 경로 한 단계씩이다.
 * import 가 타입뿐인 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/eventScene.test.ts).
 */
export const NEWS_NODE = "__news__";
/** 기사 → 언급 기업 선. 관계 종류가 아니라서 색은 화면(EventGalaxy)이 따로 칠한다 */
export const MENTION = "MENTION";
const RELATIONS = new Set(["SUPPLY", "INVEST", "PARTNER", "COMPETE", "CO_MENTIONED"]);
const MENTION_SCORE = 80;
const HOLD_SCORE = 25;

export interface EventNodeLike {
  companyId: string | null;
  name: string;
  basis: "REPORTED" | "INFERRED";
  hop: number;
  status: "OK" | "HOLD";
  anchorCompanyId: string | null;
  path: { sourceCompanyId: string | null; targetCompanyId: string | null; relationshipType: string | null; strength: number | null }[];
  abnormalReturns: { windowDays: number; abnormalReturn: number | null }[];
}

export interface EventScene {
  graph: CompanyGraph;
  reported: Set<string>;
  held: Set<string>;
  /** 기업 → 사건 다음 거래일(1일) 초과수익률(비율). 관측값이며 예측이 아니다 */
  dayOne: Map<string, number>;
}

export function eventScene(eventId: string, asOfAt: string, nodes: EventNodeLike[]): EventScene {
  const placed = nodes.filter((n): n is EventNodeLike & { companyId: string } => Boolean(n.companyId));
  const onMap = new Set(placed.map((n) => n.companyId));
  const edges = new Map<string, GraphEdge>();
  const add = (src: string, dst: string, type: string, score: number) => {
    const id = `${src}>${dst}:${type}`;
    const prev = edges.get(id);
    if (!prev || prev.score < score)
      edges.set(id, { relationshipId: id, sourceCompanyId: src, targetCompanyId: dst, relationshipType: type, score, impactDirection: null });
  };

  const reported = new Set<string>();
  const held = new Set<string>();
  const dayOne = new Map<string, number>();
  for (const n of placed) {
    const ar = n.abnormalReturns.find((r) => r.windowDays === 1)?.abnormalReturn;
    if (ar != null) dayOne.set(n.companyId, ar);
    if (n.status === "HOLD") held.add(n.companyId);
    if (n.basis === "REPORTED") {
      reported.add(n.companyId);
      add(NEWS_NODE, n.companyId, MENTION, MENTION_SCORE);
      continue;
    }
    let linked = false;
    for (const s of n.path) {
      if (!s.sourceCompanyId || !s.targetCompanyId || !onMap.has(s.sourceCompanyId) || !onMap.has(s.targetCompanyId)) continue;
      const score = n.status === "HOLD" ? HOLD_SCORE : 60 + Math.round(40 * (s.strength ?? 0.5));
      add(s.sourceCompanyId, s.targetCompanyId, relationOf(s.relationshipType), score);
      linked = true;
    }
    // 경로가 지도 밖으로 나가면(해외 기업 등) 앵커에, 앵커도 없으면 기사에 매단다 — 궤도에 홀로 뜬 행성이 없게
    if (!linked) {
      const from = n.anchorCompanyId && onMap.has(n.anchorCompanyId) ? n.anchorCompanyId : NEWS_NODE;
      add(from, n.companyId, relationOf(n.path[0]?.relationshipType ?? null), n.status === "HOLD" ? HOLD_SCORE : 50);
    }
  }

  const graph: CompanyGraph = {
    snapshotId: `event:${eventId}`,
    asOfAt,
    centerCompanyId: NEWS_NODE,
    personalized: false,
    nodes: [
      { companyId: NEWS_NODE, name: "기사", depth: 0 },
      ...placed.map((n) => ({ companyId: n.companyId, name: n.name, depth: n.basis === "REPORTED" ? 1 : 1 + Math.max(1, n.hop) })),
    ],
    edges: [...edges.values()],
  };
  return { graph, reported, held, dayOne };
}

function relationOf(type: string | null): string {
  return type && RELATIONS.has(type) ? type : "CO_MENTIONED";
}

/** 행성 이름표 — 10자 넘는 이름은 줄이고, 1일 초과수익률을 붙인다 ("SK하이닉스 −1.7%") */
export function chipName(name: string, ar: number | null): string {
  const short = name.length > 10 ? `${name.slice(0, 9)}…` : name;
  if (ar == null) return short;
  const pct = Math.abs(ar * 100).toFixed(1);
  return `${short} ${ar > 0 ? "+" : ar < 0 ? "−" : ""}${pct}%`;
}
