/**
 * 그래프 레이아웃 — 3D 씬에 올릴 노드 좌표를 계산한다.
 * 모두 결정론적이라 같은 스냅샷이면 같은 자리다 (새로고침·재조회에도 흔들리지 않는다).
 */
import type { CompanyGraph, GraphEdge, LatestGraph, LatestGraphNode, Market, RelationshipType } from "@/api/types";
import { ARMS, ARM_SPREAD, CORE_Y, DISC_Y, R_CORE, R_MAX, armTheta, nearestArm } from "./galaxyShape";
import { INDUSTRY_COLORS, industryColor, relationshipMeta } from "./meta";
import { gaussian, hashString, seededRandom } from "./rng";

export type Vec3 = [number, number, number];

export interface SceneNode {
  id: string;
  name: string;
  industry: string;
  market: Market;
  /** company.stock_code 는 DDL 상 NULL 허용 — 로고 조회에만 쓰고 표시에는 쓰지 않는다 */
  stockCode: string | null;
  color: string;
  pos: Vec3;
  /** 월드 단위 반지름 */
  size: number;
  /** 기업 중심 뷰에서의 단계 (은하 뷰는 0) */
  depth: number;
  degree: number;
  /**
   * 이름표 LOD 등급 — 0 이 가장 중요하다.
   * 은하: 질량 랭크 상위 12 는 0, 50 위까지 1, 나머지 2 / 기업 중심: min(depth, 2).
   * 멀리서는 0 만, 가까이 갈수록 1·2 등급의 칩이 나타난다.
   */
  tier: number;
  /**
   * 기간별 주가 등락률(%) — 은하 뷰의 행성 높이(three/altitude.ts)가 읽는다.
   * 기업 중심 뷰는 응답에 이 필드가 없어 undefined 이고, 그 뷰에서는 고도를 쓰지 않는다.
   */
  priceChange?: LatestGraphNode["priceChange"];
}
/** 은하 뷰의 산업 성단 하나 — 성단 중심·반지름·질량 (별가루 나선 여부 등 성단 단위 표현의 기준) */
export interface SceneCluster {
  industry: string;
  center: Vec3;
  radius: number;
  /** 구성 기업의 가중 연결도 합 — 질량 랭크·반지름 스프링과 같은 척도 */
  mass: number;
}
export interface SceneEdge {
  id: string;
  source: string;
  target: string;
  from: Vec3;
  to: Vec3;
  type: RelationshipType;
  score: number;
  impact: GraphEdge["impactDirection"];
  color: string;
  directed: boolean;
}
export interface SceneModel {
  kind: "galaxy" | "system";
  centerId: string | null;
  nodes: SceneNode[];
  edges: SceneEdge[];
  nodeById: Map<string, SceneNode>;
  /** 노드 → 인접 노드 집합 (하이라이트용) */
  neighbors: Map<string, Set<string>>;
  /** 노드 → 인접 간선 id */
  incident: Map<string, Set<string>>;
  edgeById: Map<string, SceneEdge>;
  /** 산업 성단 (은하 뷰만, 기업 중심 뷰는 빈 배열) */
  clusters: SceneCluster[];
  /** 배치 범위 — 바닥 격자 높이·크기를 정하는 데 쓴다 */
  extent: { minY: number; maxR: number };
}

const INDUSTRY_ORDER = Object.keys(INDUSTRY_COLORS);

function weightedDegree(edges: GraphEdge[]) {
  const deg = new Map<string, number>();
  const cnt = new Map<string, number>();
  edges.forEach((e) => {
    for (const id of [e.sourceCompanyId, e.targetCompanyId]) {
      deg.set(id, (deg.get(id) ?? 0) + e.score / 100);
      cnt.set(id, (cnt.get(id) ?? 0) + 1);
    }
  });
  return { deg, cnt };
}

/**
 * 행성 반지름(월드 단위) — 연결 가중치 w 와 시가총액 순위 capT(0~1, 없으면 undefined)로 정한다.
 *  - 시가총액이 있으면: 바닥 0.75 + 연결도 √w·0.36 + 시가총액 capT·1.0. 연결도가 낮아도 시가총액이 크면(capT 1) 2.1 안팎으로
 *    중간 허브만큼 커져 "큰 회사" 가 한눈에 보이고, 둘 다 큰 허브는 2.9~3.1 이다 (목업 실측: 중간값 1.35 → 1.7, 최대 2.84 → 3.0).
 *    연결도 계수가 예전(0.52)보다 작아 시가총액이 아주 작은 기업(capT ≈ 0)은 이전보다 최대 15% 작아진다 — 규모를 반영하려는 의도다
 *  - 없으면(서버가 marketCapKrw 를 아직 안 주는 live): 바닥 0.75 + √w·0.58 — 예전 공식(0.7 + √w·0.52, 상한 2.9)보다 약 10% 크다
 * 자리(랭크·질량)는 여기가 아니라 가중 연결도가 정한다 — 관계가 자리를, 크기가 규모를 말한다
 */
const SIZE_BASE = 0.75;
const SIZE_MAX = 3.4;
function nodeSize(w: number, capT: number | undefined) {
  const s = capT === undefined ? SIZE_BASE + Math.sqrt(w) * 0.58 : SIZE_BASE + Math.sqrt(w) * 0.36 + capT * 1.0;
  return Math.min(SIZE_MAX, s);
}

/**
 * 시가총액 → 0~1 순위 척도. 로그 스케일에 5·95 백분위를 양끝으로 잡아 삼성전자 같은 극단값이 나머지를 다 눌러 버리지 않게 한다.
 * 값이 있는 기업이 둘 미만이면(서버가 아직 안 주는 live) undefined 만 돌려 nodeSize 가 연결 가중치만 쓰게 한다.
 * 척도는 전체 우주 스냅샷으로 한 번 만들어 layoutGalaxy 와 기업 중심 배치(GalaxyPage 의 lookup)에 같이 넘긴다 — 산업 필터로 줄어든
 * 그래프로 따로 만들면 같은 기업이 은하 뷰와 기업 중심 뷰에서 다른 크기가 된다
 */
export type CapScale = (cap: number | null | undefined) => number | undefined;
export function capScale(nodes: { marketCapKrw?: number | null }[]): CapScale {
  const logs = nodes
    .map((n) => n.marketCapKrw)
    .filter((c): c is number => typeof c === "number" && c > 0)
    .map((c) => Math.log10(c))
    .sort((a, b) => a - b);
  if (logs.length < 2) return () => undefined;
  const lo = logs[Math.floor((logs.length - 1) * 0.05)];
  const hi = logs[Math.ceil((logs.length - 1) * 0.95)];
  if (!(hi > lo)) return () => undefined;
  return (cap) => (typeof cap === "number" && cap > 0 ? Math.min(1, Math.max(0, (Math.log10(cap) - lo) / (hi - lo))) : undefined);
}

/**
 * 겹침 완화 + 간선 인접 노드끼리 살짝 끌어당기는 결정론적 완화 단계.
 * opts.ring 을 주면(기업 중심 뷰) 매 반복 끝에 노드를 자기 궤도(반지름·SYSTEM_SQUASH 타원)로 되돌린다 — 반발·인력이 각도만 바꾸고
 * 반지름은 못 바꾸게 해서, 이웃이 많은 허브가 중심 쪽으로 끌려 들어가 궤도가 뭉개지는 일을 막는다
 */
function relax(nodes: SceneNode[], edges: GraphEdge[], anchor: Map<string, Vec3>, iterations: number, opts: { spring: number; attract: number; planar: boolean; ring?: Map<string, number> }) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (let it = 0; it < iterations; it += 1) {
    // 반발
    for (let i = 0; i < nodes.length; i += 1) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j += 1) {
        const b = nodes[j];
        const dx = b.pos[0] - a.pos[0];
        const dy = b.pos[1] - a.pos[1];
        const dz = b.pos[2] - a.pos[2];
        const d2 = dx * dx + dy * dy + dz * dz;
        const min = (a.size + b.size) * 4.6 + 7;
        if (d2 < min * min && d2 > 1e-6) {
          const d = Math.sqrt(d2);
          const push = ((min - d) / d) * 0.5;
          a.pos[0] -= dx * push;
          a.pos[2] -= dz * push;
          b.pos[0] += dx * push;
          b.pos[2] += dz * push;
          if (!opts.planar) {
            a.pos[1] -= dy * push * 0.5;
            b.pos[1] += dy * push * 0.5;
          }
        }
      }
    }
    // 간선 인접 끌림
    edges.forEach((e) => {
      const a = byId.get(e.sourceCompanyId);
      const b = byId.get(e.targetCompanyId);
      if (!a || !b) return;
      const k = opts.attract * (e.score / 100);
      const dx = b.pos[0] - a.pos[0];
      const dz = b.pos[2] - a.pos[2];
      a.pos[0] += dx * k;
      a.pos[2] += dz * k;
      b.pos[0] -= dx * k;
      b.pos[2] -= dz * k;
    });
    // 원위치 스프링 (성단 밖으로 나가지 않도록)
    nodes.forEach((n) => {
      const o = anchor.get(n.id)!;
      n.pos[0] += (o[0] - n.pos[0]) * opts.spring;
      n.pos[1] += (o[1] - n.pos[1]) * opts.spring;
      n.pos[2] += (o[2] - n.pos[2]) * opts.spring;
      const ring = opts.ring?.get(n.id);
      if (ring !== undefined) {
        const th = Math.atan2(n.pos[2] / SYSTEM_SQUASH, n.pos[0]);
        n.pos[0] = Math.cos(th) * ring;
        n.pos[2] = Math.sin(th) * ring * SYSTEM_SQUASH;
      }
    });
  }
}

function finish(kind: SceneModel["kind"], centerId: string | null, nodes: SceneNode[], edges: GraphEdge[], clusters: SceneCluster[]): SceneModel {
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  const neighbors = new Map<string, Set<string>>();
  const incident = new Map<string, Set<string>>();
  const sceneEdges: SceneEdge[] = [];
  edges.forEach((e) => {
    const a = nodeById.get(e.sourceCompanyId);
    const b = nodeById.get(e.targetCompanyId);
    if (!a || !b) return;
    const meta = relationshipMeta(e.relationshipType);
    sceneEdges.push({
      id: e.relationshipId,
      source: a.id,
      target: b.id,
      from: a.pos,
      to: b.pos,
      type: e.relationshipType,
      score: e.score,
      impact: e.impactDirection,
      color: meta.color,
      directed: meta.directed,
    });
    for (const [x, y] of [
      [a.id, b.id],
      [b.id, a.id],
    ]) {
      if (!neighbors.has(x)) neighbors.set(x, new Set());
      neighbors.get(x)!.add(y);
      if (!incident.has(x)) incident.set(x, new Set());
      incident.get(x)!.add(e.relationshipId);
    }
  });
  sceneEdges.sort((a, b) => a.score - b.score); // 강한 간선이 나중에(위에) 그려지도록
  const edgeById = new Map(sceneEdges.map((e) => [e.id, e]));
  // 노드가 없으면 0 — 격자 쪽에서 나눗셈에 쓰지 않도록 유한값을 보장한다
  let minY = 0;
  let maxR = 0;
  nodes.forEach((n, i) => {
    minY = i === 0 ? n.pos[1] : Math.min(minY, n.pos[1]);
    maxR = Math.max(maxR, Math.hypot(n.pos[0], n.pos[2]));
  });
  return { kind, centerId, nodes, edges: sceneEdges, nodeById, neighbors, incident, edgeById, clusters, extent: { minY, maxR } };
}

/* ------------------------------------------------------------------ */
/* 은하 — 연관도가 자리를 정하는 납작한 나선 원반                             */
/* ------------------------------------------------------------------ */
/** 핵(벌지)에 앉는 최상위 기업 수 */
const CORE_N = 6;
/** 힘 시뮬레이션 반복 수와 속도 감쇠 */
const SIM_STEPS = 260;
const SIM_DAMP = 0.85;
/**
 * 힘 → 이동 환산 계수. 감쇠 0.85 의 정상 상태 증폭이 1/(1-0.85)≈6.7 배라 힘을 그대로 더하면
 * 한 스텝에 수십 유닛을 튀어 배치가 폭발한다. 여기서 한 번 줄여 스텝당 1 유닛 안팎으로 만든다.
 */
const SIM_STEP = 0.18;
/** 한 스텝 최대 이동 — 겹친 노드가 서로를 걷어차는 순간의 안전판 */
const MAX_STEP = 4;
/** 반발 사거리와 세기 — 사거리를 34→40 으로 늘려 이웃 행성이 더 떨어져 앉는다 (스프링 목표·하드 간격·핵 사다리도 함께 키웠다) */
const REP_D = 40;
const REP_K = 0.9;
/** 간선 스프링 세기 — 목표 거리는 점수 100 → 11, 점수 0 → 32 */
const SPRING_K = 0.06;
/**
 * 팔 중심선으로 모으는 접선 인력.
 * 세면 모든 행성이 중심선 위 한 줄로 붙어 '그려 넣은 선' 처럼 보인다 — 초기 각도 분포(ARM_SPREAD)를
 * 시뮬레이션이 다시 짜부라뜨리지 않을 만큼만 남긴다.
 */
const ARM_K = 0.018;
/** 팔 인력을 아예 받지 않는 '팔 사이' 노드 비율 — 실제 은하처럼 팔 사이도 드물게 채운다 */
const INTER_ARM_SHARE = 0.15;
/** 랭크 반지름에 주는 시드 지터 폭 — 랭크대로 줄 세우면 반지름이 계단처럼 보인다 */
const RADIUS_JITTER = 0.12;
/** 랭크 반지름으로 되돌리는 반지름 스프링 (질량이 클수록 강하다) */
const RADIUS_K = 0.03;
/** 마지막 이 횟수 동안은 하드 최소 간격을 직접 밀어 지킨다 */
const HARD_TAIL = 40;
/** 하드 최소 간격 = (반지름 합) × 이 값 + 여백 — 칩(화면 26px)과 행성이 겹치지 않고 이웃이 한눈에 구분되는 간격 */
const GAP_K = 4.0;
const GAP_PAD = 5;
/**
 * 핵 사다리의 최소 간격 배율 — 핵 6개는 하드 간격 패스가 밀지 않으므로(고정) 사다리를 늘릴 때 이 배율로 벌린다.
 * 기본 자세에서 이름표가 보이는 것은 핵 허브들뿐이라 여기가 벌어져야 "간격이 넓어졌다" 가 눈에 보인다.
 * 목업(허브 반지름 2.6~3.0)에서는 이 요구치(배율 ≈ 3.5 이상)를 CORE_SCALE_MAX 2.8 이 먼저 막아 실제 사다리는 2.8 배로 고정된다 —
 * 즉 지금 벌지 크기는 CORE_SCALE_MAX 가 정하고, 이 값은 허브가 더 작은 데이터에서만 효력이 있다.
 * 결과(실측): 핵 최소 쌍거리 8.3 → 11.7, 기본 자세 화면 간격 +28%, 팔 시작 반지름 32 → 46
 */
const CORE_GAP_K = 1.8;
/** 팔 배정 그리디의 부하 벌점 — 없으면 친화도가 큰 팔 하나로 전부 몰려 나선이 한 줄만 굵어진다 */
const ARM_BALANCE = 0.4;
/** 핵 사다리를 늘리는 배율의 상한 — 벌지가 원반만큼 커지지 않게 (2.8 이면 바깥 허브 r ≈ 36, 팔 시작 r ≈ 46 으로 원반 반지름의 약 27%) */
const CORE_SCALE_MAX = 2.8;
/**
 * 핵 6개가 처음 각도에서 벗어날 수 있는 한계(rad).
 * 반지름을 고정한 채 각도를 풀어 주면 이웃 두 허브가 같은 방위로 모여 붙어 버린다(실측 3.9 유닛).
 * 이 한계 안에서만 돌게 하고, 아래 coreScale 을 "최악으로 좁혀졌을 때" 기준으로 잡아 겹침을 원천 차단한다.
 */
const CORE_DRIFT = 0.12;

/**
 * 전체 은하 — 아스트라 사진처럼 납작한 3팔 나선 원반.
 *  - 자리를 정하는 것은 산업이 아니라 관계다. 간선 점수가 높을수록 짧은 스프링으로 묶여 가까이 앉는다
 *  - 반지름은 질량(가중 연결도) 랭크가 정한다. 허브 6개가 핵에 모이고 잎이 팔 끝으로 밀린다
 *  - 행성 크기(nodeSize)는 연결 가중치에 시가총액(marketCapKrw, 원화 환산·로그 순위)을 더해 정한다 — 자리와는 별개다
 *  - 산업은 "어느 팔에서 출발할지" 만 정하고(관계가 많은 산업끼리 같은 팔), 나머지는 시뮬레이션이 결정한다
 *  - 구가 아니라 원반이라 위에서 비스듬히 보면 어떤 행성도 다른 행성 뒤에 숨지 않는다 (허브 클릭 가능)
 */
/** @param capT 시가총액 척도 — 전체 우주로 만든 것을 넘긴다(GalaxyPage). 생략하면 이 그래프의 노드만으로 만든다 */
export function layoutGalaxy(graph: LatestGraph, capT: CapScale = capScale(graph.nodes)): SceneModel {
  const rng = seededRandom(hashString(graph.snapshotId));
  const { deg, cnt } = weightedDegree(graph.edges);
  const n = graph.nodes.length;
  if (n === 0) return finish("galaxy", null, [], graph.edges, []);

  /* 1) 질량 랭크 — 질량은 가중 연결도다(시가총액은 크기에만 들어간다). 같은 질량이면 id 순으로 끊어 같은 스냅샷이면 랭크도 같다 */
  const massOf = (id: string) => deg.get(id) ?? 0;
  const sizeOf = new Map(graph.nodes.map((c) => [c.companyId, nodeSize(massOf(c.companyId), capT(c.marketCapKrw))]));
  const ranked = graph.nodes.slice().sort((a, b) => {
    const d = massOf(b.companyId) - massOf(a.companyId);
    return d !== 0 ? d : a.companyId < b.companyId ? -1 : 1;
  });
  const index = new Map(ranked.map((c, i) => [c.companyId, i]));
  const maxMass = Math.max(1e-6, massOf(ranked[0].companyId));

  /* 2) 산업 → 팔 (그리디). 산업 간 간선 가중치 합이 큰 산업끼리 같은 팔에서 출발한다 */
  const industryOf = new Map(graph.nodes.map((c) => [c.companyId, c.industryName]));
  const groups = new Map<string, number>();
  graph.nodes.forEach((c) => groups.set(c.industryName, (groups.get(c.industryName) ?? 0) + 1));
  const industries = [...groups.keys()].sort((a, b) => {
    const ia = INDUSTRY_ORDER.indexOf(a);
    const ib = INDUSTRY_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const between = new Map<string, number>();
  graph.edges.forEach((e) => {
    const a = industryOf.get(e.sourceCompanyId);
    const b = industryOf.get(e.targetCompanyId);
    if (!a || !b || a === b) return;
    const key = a < b ? `${a} ${b}` : `${b} ${a}`;
    between.set(key, (between.get(key) ?? 0) + e.score / 100);
  });
  const affinity = (a: string, b: string) => between.get(a < b ? `${a} ${b}` : `${b} ${a}`) ?? 0;
  // 산업이 하나뿐인 필터 뷰는 팔을 만들 재료가 없다 — 팔 인력을 끄고 원반을 노드 수에 맞춰 줄인다
  const single = industries.length <= 1;
  const armOf = new Map<string, number>();
  if (!single) {
    const load = new Array<number>(ARMS).fill(0);
    const order = industries.slice().sort((a, b) => groups.get(b)! - groups.get(a)! || (a < b ? -1 : 1));
    order.forEach((ind) => {
      let best = 0;
      let bestScore = -Infinity;
      for (let k = 0; k < ARMS; k += 1) {
        let aff = 0;
        armOf.forEach((ak, other) => {
          if (ak === k) aff += affinity(ind, other);
        });
        const s = aff - load[k] * ARM_BALANCE;
        if (s > bestScore) {
          bestScore = s;
          best = k;
        }
      }
      armOf.set(ind, best);
      load[best] += groups.get(ind)!;
    });
  }

  /* 3) 랭크 반지름. 핵 6개는 60° 간격 사다리 위에 고정하고, 나머지는 sqrt 로 바깥까지 고르게 편다 */
  const rMax = single ? Math.min(R_MAX, 30 + Math.sqrt(n) * 10) : R_MAX;
  const coreCount = Math.min(CORE_N, n);
  const phase = rng() * Math.PI * 2;
  const coreBase = (i: number) => 4 + i * 1.8;
  const coreAngle = (i: number) => (i * Math.PI) / 3 + phase;
  // 사다리를 그대로 쓰면 rank0(r=4)·rank1(r=5.8)의 거리가 5.1 뿐이라 가장 큰 두 행성이 겹친다.
  // 사다리 전체를 같은 배율로 늘려 "반지름 합 + 여백" 을 확보한다 (모양은 유지, 크기만 확대).
  // 각도는 CORE_DRIFT 만큼 서로 다가올 수 있으므로 그만큼 좁혀진 최악의 배치로 잰다
  let coreScale = 1;
  for (let i = 0; i < coreCount; i += 1) {
    for (let j = i + 1; j < coreCount; j += 1) {
      const ri = coreBase(i);
      const rj = coreBase(j);
      const gap = Math.max(0, Math.abs(((((coreAngle(i) - coreAngle(j) + Math.PI) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)) - Math.PI) - 2 * CORE_DRIFT);
      const d = Math.sqrt(Math.max(0, ri * ri + rj * rj - 2 * ri * rj * Math.cos(gap)));
      const need = (sizeOf.get(ranked[i].companyId)! + sizeOf.get(ranked[j].companyId)!) * CORE_GAP_K + GAP_PAD;
      if (d > 1e-3) coreScale = Math.max(coreScale, need / d);
    }
  }
  coreScale = Math.min(coreScale, CORE_SCALE_MAX);
  // 팔의 시작 반지름 — 핵 사다리 바깥보다 안쪽에서 출발하면 벌지 안에 팔이 파묻힌다. 바깥 허브와 첫 팔 행성 사이에도 여백(GAP_PAD 두 배)을 둔다
  const rStart = Math.max(R_CORE, coreBase(coreCount - 1) * coreScale + GAP_PAD * 2);
  // 기업이 몇 개 없는 필터 뷰에서 rMax 가 rStart 보다 작아지면 랭크가 커질수록 안으로 들어간다 — 최소 폭을 보장한다
  const rOuter = Math.max(rMax, rStart + 20);

  const rRank = new Float64Array(n);
  const x = new Float64Array(n);
  const z = new Float64Array(n);
  const vx = new Float64Array(n);
  const vz = new Float64Array(n);
  const fx = new Float64Array(n);
  const fz = new Float64Array(n);
  const mass = new Float64Array(n);
  const rad = new Float64Array(n);
  // 팔 인력을 면제받는 노드 — 시드 난수라 같은 스냅샷이면 늘 같은 기업이 팔 사이에 남는다
  const interArm = new Uint8Array(n);
  for (let i = 0; i < n; i += 1) {
    const c = ranked[i];
    const size = sizeOf.get(c.companyId)!;
    rad[i] = size;
    mass[i] = massOf(c.companyId);
    let r: number;
    let th: number;
    if (i < coreCount) {
      r = coreBase(i) * coreScale;
      th = coreAngle(i);
    } else {
      // 랭크 반지름에 ±RADIUS_JITTER 를 섞어 랭크가 만드는 동심원 계단을 흐린다
      r = rStart + (rOuter - rStart) * Math.sqrt((i - coreCount) / Math.max(1, n - coreCount));
      r = Math.max(rStart, Math.min(rOuter, r * (1 + (rng() * 2 - 1) * RADIUS_JITTER)));
      interArm[i] = rng() < INTER_ARM_SHARE ? 1 : 0;
      const arm = armOf.get(c.industryName) ?? 0;
      // 각도 산포는 반지름에 비례(ARM_SPREAD)해 팔이 바깥으로 갈수록 넓어진다
      th = (single ? phase + (i / n) * Math.PI * 2 : armTheta(arm, r)) + gaussian(rng) * ARM_SPREAD(r);
    }
    rRank[i] = r;
    x[i] = Math.cos(th) * r;
    z[i] = Math.sin(th) * r;
  }

  /* 4) 간선을 인덱스 공간으로 — 스프링 목표 거리는 점수가 높을수록 짧다 */
  const ea: number[] = [];
  const eb: number[] = [];
  const ew: number[] = [];
  const ed0: number[] = [];
  graph.edges.forEach((e) => {
    const a = index.get(e.sourceCompanyId);
    const b = index.get(e.targetCompanyId);
    if (a === undefined || b === undefined || a === b) return;
    const w = e.score / 100;
    ea.push(a);
    eb.push(b);
    ew.push(w);
    ed0.push(11 + (1 - w) * 21);
  });

  /* 5) 2D(xz) 힘 시뮬레이션 */
  for (let it = 0; it < SIM_STEPS; it += 1) {
    fx.fill(0);
    fz.fill(0);
    // a) 간선 인력 — 점수가 곧 가중치라 강한 관계일수록 짧고 세게 묶인다
    for (let k = 0; k < ea.length; k += 1) {
      const a = ea[k];
      const b = eb[k];
      const dx = x[b] - x[a];
      const dz = z[b] - z[a];
      const d = Math.max(1e-3, Math.hypot(dx, dz));
      const f = (SPRING_K * ew[k] * (d - ed0[k])) / d / 2;
      fx[a] += dx * f;
      fz[a] += dz * f;
      fx[b] -= dx * f;
      fz[b] -= dz * f;
    }
    // b) 반발 — 사거리로 정규화한다. (REP_D-d)/d 그대로면 근거리 힘이 스프링의 수십 배라
    //    모든 간선이 사거리 끝(REP_D)에 붙어 '점수가 높을수록 가깝다' 가 사라진다
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const dx = x[j] - x[i];
        const dz = z[j] - z[i];
        const d2 = dx * dx + dz * dz;
        if (d2 >= REP_D * REP_D) continue;
        const d = Math.max(0.5, Math.sqrt(d2));
        const f = (REP_K * (REP_D - d)) / REP_D / d / 2;
        fx[i] -= dx * f;
        fz[i] -= dz * f;
        fx[j] += dx * f;
        fz[j] += dz * f;
      }
    }
    // d) 반지름 스프링 — 허브가 바깥으로 밀려나지 않게 질량이 클수록 강하게 랭크 반지름으로 되돌린다
    for (let i = 0; i < n; i += 1) {
      const r = Math.hypot(x[i], z[i]);
      if (r < 1e-3) continue;
      const f = (RADIUS_K * (0.5 + mass[i] / maxMass) * (rRank[i] - r)) / r;
      fx[i] += x[i] * f;
      fz[i] += z[i] * f;
    }
    // 적분
    for (let i = 0; i < n; i += 1) {
      let nvx = (vx[i] + fx[i] * SIM_STEP) * SIM_DAMP;
      let nvz = (vz[i] + fz[i] * SIM_STEP) * SIM_DAMP;
      const sp = Math.hypot(nvx, nvz);
      if (sp > MAX_STEP) {
        nvx = (nvx / sp) * MAX_STEP;
        nvz = (nvz / sp) * MAX_STEP;
      }
      vx[i] = nvx;
      vz[i] = nvz;
      x[i] += nvx;
      z[i] += nvz;
    }
    // c) 팔 인력 — 반지름은 그대로 두고 각도만 팔 중심선 쪽으로 접선 이동시킨다
    if (!single) {
      for (let i = coreCount; i < n; i += 1) {
        if (interArm[i]) continue;
        const r = Math.hypot(x[i], z[i]);
        if (r < 1e-3) continue;
        const th = Math.atan2(z[i], x[i]);
        const nth = th + ARM_K * nearestArm(th, r).delta;
        x[i] = Math.cos(nth) * r;
        z[i] = Math.sin(nth) * r;
      }
    }
    // e) 핵은 반지름 고정 — 각도만 CORE_DRIFT 안에서 힘을 따라 조금씩 돈다
    for (let i = 0; i < coreCount; i += 1) {
      const base = coreAngle(i);
      const r = Math.hypot(x[i], z[i]);
      const th = r < 1e-3 ? base : Math.atan2(z[i], x[i]);
      let d = (th - base) % (Math.PI * 2);
      if (d > Math.PI) d -= Math.PI * 2;
      if (d < -Math.PI) d += Math.PI * 2;
      const clamped = base + Math.max(-CORE_DRIFT, Math.min(CORE_DRIFT, d));
      x[i] = Math.cos(clamped) * rRank[i];
      z[i] = Math.sin(clamped) * rRank[i];
    }
    // b-하드) 마지막 구간에서는 최소 간격을 직접 밀어 지킨다. 핵은 이미 겹치지 않게 벌려 두었으므로 움직이지 않는다
    if (it >= SIM_STEPS - HARD_TAIL) {
      // 한 번의 가우스-자이델 패스로는 서로 밀어낸 노드가 또 다른 노드를 파고든다 — 두 번 훑어 잔여 위반을 줄인다
      for (let pass = 0; pass < 2; pass += 1) {
        for (let i = 0; i < n; i += 1) {
          for (let j = i + 1; j < n; j += 1) {
            const min = (rad[i] + rad[j]) * GAP_K + GAP_PAD;
            const dx = x[j] - x[i];
            const dz = z[j] - z[i];
            const d2 = dx * dx + dz * dz;
            if (d2 >= min * min) continue;
            const d = Math.max(0.5, Math.sqrt(d2));
            const push = (min - d) / d;
            const wi = i < coreCount ? 0 : j < coreCount ? 1 : 0.5;
            const wj = j < coreCount ? 0 : i < coreCount ? 1 : 0.5;
            x[i] -= dx * push * wi;
            z[i] -= dz * push * wi;
            x[j] += dx * push * wj;
            z[j] += dz * push * wj;
          }
        }
      }
    }
    // f) 벌지 안쪽 금지선 — 강한 간선에 끌려 핵 사다리 안으로 가라앉은 노드는 고정된 핵 6개와 겹쳐도
    //    하드 간격이 밀어낼 곳이 없다(핵은 움직이지 않는다). 팔 시작 반지름 밖으로 되돌려 벌지를 비운다
    // 비핵 노드의 rRank 는 초기화에서 이미 rStart 이상으로 클램프되므로 금지선은 rStart 하나로 충분하다
    for (let i = coreCount; i < n; i += 1) {
      const r = Math.hypot(x[i], z[i]);
      if (r >= rStart || r < 1e-3) continue;
      x[i] = (x[i] / r) * rStart;
      z[i] = (z[i] / r) * rStart;
    }
  }

  /* 6) 높이 — 핵은 두툼한 벌지, 팔은 얇은 원반. 랭크 순으로 뽑아 결정론적이다 */
  const nodes: SceneNode[] = [];
  for (let i = 0; i < n; i += 1) {
    const c = ranked[i];
    const r = Math.hypot(x[i], z[i]);
    // 가우시안 꼬리를 그대로 두면 한두 행성이 원반 위로 튀어 나와 납작함이 깨진다
    const g = Math.max(-2, Math.min(2, gaussian(rng)));
    // 벌지 여부는 반지름이 아니라 핵 인덱스로 가른다 — coreScale 로 벌린 핵 사다리는 R_CORE 밖까지 나가므로
    // r 로 재면 핵 6개 중 뒤쪽 4개가 팔의 얇은 두께를 받아 "핵은 두툼한 벌지" 가 반만 지켜진다
    const y = g * (i < coreCount ? CORE_Y : DISC_Y(r));
    nodes.push({
      id: c.companyId,
      name: c.name,
      industry: c.industryName,
      market: c.market,
      stockCode: c.stockCode,
      color: industryColor(c.industryName),
      pos: [x[i], y, z[i]],
      size: rad[i],
      depth: 0,
      degree: cnt.get(c.companyId) ?? 0,
      tier: i < 12 ? 0 : i < 50 ? 1 : 2,
      priceChange: c.priceChange,
    });
  }

  /* 7) 산업 성단 — 중심·반지름·질량 요약. 관계로 흩어진 산업은 반지름이 커진다 */
  const byIndustry = new Map<string, SceneNode[]>();
  nodes.forEach((nd) => {
    if (!byIndustry.has(nd.industry)) byIndustry.set(nd.industry, []);
    byIndustry.get(nd.industry)!.push(nd);
  });
  const clusters: SceneCluster[] = [];
  byIndustry.forEach((members, ind) => {
    let cx = 0;
    let cy = 0;
    let cz = 0;
    let m = 0;
    members.forEach((nd) => {
      cx += nd.pos[0];
      cy += nd.pos[1];
      cz += nd.pos[2];
      m += massOf(nd.id);
    });
    cx /= members.length;
    cy /= members.length;
    cz /= members.length;
    let radius = 0;
    members.forEach((nd) => {
      radius = Math.max(radius, Math.hypot(nd.pos[0] - cx, nd.pos[2] - cz));
    });
    clusters.push({ industry: ind, center: [cx, cy, cz], radius: radius + 8, mass: m });
  });

  return finish("galaxy", null, nodes, graph.edges, clusters);
}

/* ------------------------------------------------------------------ */
/* 기업 중심 — depth 가 궤도 반지름이 된다                                  */
/* ------------------------------------------------------------------ */
/** depth 별 궤도 반지름의 기본값 — 한 궤도에 행성이 많으면 layoutSystem 이 둘레를 확보하도록 키우고 바깥 궤도를 그만큼 밀어낸다 */
export const ORBIT = [0, 32, 58, 82];
/** 궤도 타원의 z 축 비율 — 비스듬히 내려다볼 때 원으로 보이도록 살짝 눌렀다 */
const SYSTEM_SQUASH = 0.82;
/** 궤도 둘레에서 행성 하나가 차지하는 길이 — 이보다 촘촘해지면 궤도 반지름을 키운다 (행성 지름 4~7 + 이름표 여유) */
const RING_SPACING = 16;
/** 이웃 궤도 사이 최소 간격 — 안쪽 궤도가 커지면 바깥 궤도도 이만큼은 밀려난다 */
const RING_GAP = 24;

/**
 * 기업 중심 배치가 산업·시장·종목코드·시가총액 순위를 채우는 데 쓰는 조회 함수 (전체 우주 스냅샷에서 만든다).
 * capT 는 capScale 로 만든 0~1 척도 — 은하 뷰와 같은 척도라야 워프 전후로 행성 크기가 튀지 않는다
 */
export type CompanyLookup = (id: string) => { name?: string; industryName: string; market: Market; stockCode: string | null; capT?: number } | undefined;

export function layoutSystem(graph: CompanyGraph, lookup: CompanyLookup): SceneModel {
  const rng = seededRandom(hashString(graph.snapshotId + graph.centerCompanyId));
  const { deg, cnt } = weightedDegree(graph.edges);
  // depth 는 응답값과 "돌려받은 간선 기준 최단 홉" 중 작은 쪽 — 서버가 확장 폭을 제한해 나중 단계에 넣은 기업이 중심과 직접 간선을
  // 갖는 경우, 그 기업을 depth 1 궤도에 앉혀야 중심에서 바깥 궤도까지 뻗는 긴 빔이 생기지 않는다 (계약: depth 1 = 직접 관계)
  const adjacency = new Map<string, string[]>();
  graph.edges.forEach((e) => {
    for (const [a, b] of [[e.sourceCompanyId, e.targetCompanyId], [e.targetCompanyId, e.sourceCompanyId]]) {
      if (!adjacency.has(a)) adjacency.set(a, []);
      adjacency.get(a)!.push(b);
    }
  });
  const hop = new Map<string, number>([[graph.centerCompanyId, 0]]);
  for (let queue = [graph.centerCompanyId]; queue.length; ) {
    const next: string[] = [];
    for (const id of queue) for (const o of adjacency.get(id) ?? []) if (!hop.has(o)) {
      hop.set(o, hop.get(id)! + 1);
      next.push(o);
    }
    queue = next;
  }
  const depthOf = new Map(graph.nodes.map((n) => [n.companyId, Math.min(n.depth, hop.get(n.companyId) ?? n.depth)]));

  // 각 노드의 "부모": 더 낮은 depth 의 이웃 중 점수가 가장 높은 것
  const parent = new Map<string, string>();
  const bestScore = new Map<string, number>();
  graph.edges.forEach((e) => {
    const ds = depthOf.get(e.sourceCompanyId) ?? 9;
    const dt = depthOf.get(e.targetCompanyId) ?? 9;
    if (ds === dt) return;
    const [child, par] = ds > dt ? [e.sourceCompanyId, e.targetCompanyId] : [e.targetCompanyId, e.sourceCompanyId];
    if ((bestScore.get(child) ?? -1) < e.score) {
      bestScore.set(child, e.score);
      parent.set(child, par);
    }
  });

  const angle = new Map<string, number>([[graph.centerCompanyId, 0]]);
  const byDepth = new Map<number, string[]>();
  graph.nodes.forEach((n) => {
    const d = depthOf.get(n.companyId)!;
    if (!byDepth.has(d)) byDepth.set(d, []);
    byDepth.get(d)!.push(n.companyId);
  });
  const maxDepth = Math.max(...byDepth.keys());
  for (let d = 1; d <= maxDepth; d += 1) {
    const ids = (byDepth.get(d) ?? []).slice();
    if (d === 1) {
      ids.sort((a, b) => (deg.get(b) ?? 0) - (deg.get(a) ?? 0));
      ids.forEach((id, i) => angle.set(id, (i / ids.length) * Math.PI * 2 - Math.PI / 2));
      continue;
    }
    // 부모 각도 주변에 자식을 부채꼴로 배치
    const byParent = new Map<string, string[]>();
    ids.forEach((id) => {
      const p = parent.get(id) ?? graph.centerCompanyId;
      if (!byParent.has(p)) byParent.set(p, []);
      byParent.get(p)!.push(id);
    });
    const sector = (Math.PI * 2) / Math.max(1, byDepth.get(d - 1)?.length ?? 1);
    byParent.forEach((children, p) => {
      const base = angle.get(p) ?? rng() * Math.PI * 2;
      children.forEach((c, i) => {
        const t = children.length === 1 ? 0 : i / (children.length - 1) - 0.5;
        angle.set(c, base + t * sector * 0.85);
      });
    });
  }

  // 궤도 반지름 — 기본값(ORBIT)에서 출발해, 그 궤도의 행성 수가 둘레(RING_SPACING × 수)를 넘치면 키우고 바깥 궤도는 RING_GAP 만큼 뒤로 민다
  const ringR: number[] = [0];
  for (let d = 1; d <= maxDepth; d += 1) {
    const count = byDepth.get(d)?.length ?? 0;
    ringR[d] = Math.max(ORBIT[Math.min(d, ORBIT.length - 1)], ringR[d - 1] + RING_GAP, (count * RING_SPACING) / (Math.PI * 2));
  }

  const nodes: SceneNode[] = [];
  const anchor = new Map<string, Vec3>();
  const ring = new Map<string, number>();
  graph.nodes.forEach((n) => {
    const d = depthOf.get(n.companyId)!;
    const r = d === 0 ? 0 : ringR[d] + (rng() - 0.5) * 6;
    if (d > 0) ring.set(n.companyId, r);
    const a = angle.get(n.companyId) ?? rng() * Math.PI * 2;
    const pos: Vec3 = d === 0 ? [0, 0, 0] : [Math.cos(a) * r, (rng() - 0.5) * (5 + d * 4), Math.sin(a) * r * SYSTEM_SQUASH];
    anchor.set(n.companyId, [...pos] as Vec3);
    const info = lookup(n.companyId);
    nodes.push({
      id: n.companyId,
      name: n.name,
      industry: info?.industryName ?? "",
      market: info?.market ?? "",
      stockCode: info?.stockCode ?? null,
      color: industryColor(info?.industryName),
      pos,
      size: d === 0 ? 3.4 : nodeSize(deg.get(n.companyId) ?? 0, info?.capT) * (d === 1 ? 1.15 : d === 2 ? 0.95 : 0.8),
      depth: d,
      degree: cnt.get(n.companyId) ?? 0,
      tier: Math.min(d, 2),
    });
  });
  const movable = nodes.filter((n) => n.depth > 0);
  relax(movable, graph.edges, anchor, 60, { spring: 0.05, attract: 0.0015, planar: false, ring });
  return finish("system", graph.centerCompanyId, nodes, graph.edges, []);
}

const SYSTEM_CACHE = new WeakMap<CompanyGraph, { lookup: CompanyLookup; model: SceneModel }>();
/**
 * 그래프 객체 기준 배치 캐시.
 * 워프 목적지(useCompanyGraph(warpTarget))와 착지 후 포커스(useCompanyGraph(focusId))는 react-query 에서 같은
 * CompanyGraph 참조를 받는다. 그때 같은 SceneModel 을 돌려주어야 커밋 뒤에도 씬이 리마운트되지 않고
 * (= 행성 팝인 없이) 모프가 그대로 이어진다. lookup 이 바뀌면(우주 스냅샷 도착) 다시 만든다.
 */
export function layoutSystemCached(graph: CompanyGraph, lookup: CompanyLookup): SceneModel {
  const hit = SYSTEM_CACHE.get(graph);
  if (hit && hit.lookup === lookup) return hit.model;
  const model = layoutSystem(graph, lookup);
  SYSTEM_CACHE.set(graph, { lookup, model });
  return model;
}

/* ------------------------------------------------------------------ */
/* 뉴스 부분 그래프 — 선택 뉴스의 관련 기업 + 1홉 이웃                        */
/* ------------------------------------------------------------------ */
export function layoutNewsSubgraph(graph: LatestGraph, seedIds: string[], expand: boolean): SceneModel {
  const seed = new Set(seedIds.filter((id) => graph.nodes.some((n) => n.companyId === id)));
  const included = new Set(seed);
  if (expand) {
    graph.edges.forEach((e) => {
      if (seed.has(e.sourceCompanyId)) included.add(e.targetCompanyId);
      if (seed.has(e.targetCompanyId)) included.add(e.sourceCompanyId);
    });
  }
  const edges = graph.edges.filter((e) => included.has(e.sourceCompanyId) && included.has(e.targetCompanyId));
  const nodesRaw = graph.nodes.filter((n) => included.has(n.companyId));
  const sub: CompanyGraph = {
    snapshotId: graph.snapshotId + seedIds.join(","),
    asOfAt: graph.asOfAt,
    centerCompanyId: seedIds[0] ?? "",
    personalized: graph.personalized,
    nodes: nodesRaw.map((n) => ({ companyId: n.companyId, name: n.name, depth: seed.has(n.companyId) ? (n.companyId === seedIds[0] ? 0 : 1) : 2 })),
    edges,
  };
  const info = new Map(graph.nodes.map((n) => [n.companyId, n]));
  const model = layoutSystem(sub, (id) => info.get(id));
  // 뉴스 지도는 중심 기업 하나가 아니라 관련 기업 묶음이므로 depth 0·1을 같은 궤도에 놓는다
  return model;
}

/* ------------------------------------------------------------------ */
/* 개인 은하 — 관심 기업(씨앗) + 1홉 이웃                                   */
/* ------------------------------------------------------------------ */
/**
 * 내 은하 — 우주 스냅샷에서 관심 기업 주변만 잘라 전체 은하와 같은 나선 원반으로 배치한다.
 *  - 개인 별자리는 "씨앗(관심 기업) + 1홉" 이다. 이웃까지 넣어야 관심 기업끼리 직접 연결이 없어도 무엇으로 이어지는지 보인다
 *  - 시드(snapshotId)에 관심 기업 목록을 섞는다 — 같은 관심 목록이면 늘 같은 자리(결정론)이고, 전체 은하와는 다른 자리다
 *  - 이름표 등급: 관심 기업은 0(멀리서도 항상), 이웃은 2 이상(가까이 가야 칩이 뜬다) — 내 별이 무엇인지 한눈에 갈린다
 * 자리·크기는 layoutGalaxy 가 정하고 여기서는 등급만 다시 매긴다. capT 는 호출자(전체 우주 척도)가 넘겨 줘야
 * 같은 기업이 전체 은하와 내 은하에서 같은 크기로 보인다.
 * @returns missing 우주 스냅샷(상위 N개) 밖이라 배치할 자리가 없는 관심 기업 id (입력 순서 유지) · neighborIds 1홉으로 딸려 온 기업 id
 */
export function layoutMyGalaxy(
  graph: LatestGraph,
  watchIds: string[],
  includeNeighbors: boolean,
  capT: CapScale,
): { model: SceneModel; missing: string[]; neighborIds: string[] } {
  const inSnapshot = new Set(graph.nodes.map((n) => n.companyId));
  const watched = watchIds.filter((id) => inSnapshot.has(id));
  const missing = watchIds.filter((id) => !inSnapshot.has(id));
  const seed = new Set(watched);
  // 관심 기업이 하나도 없으면 배치할 것이 없다 — 빈 모델을 돌려주고 화면은 빈 상태 카드를 보여 준다
  if (!seed.size) return { model: layoutGalaxy({ ...graph, nodes: [], edges: [] }, capT), missing, neighborIds: [] };

  const included = new Set(seed);
  if (includeNeighbors) {
    graph.edges.forEach((e) => {
      if (seed.has(e.sourceCompanyId)) included.add(e.targetCompanyId);
      if (seed.has(e.targetCompanyId)) included.add(e.sourceCompanyId);
    });
  }
  const nodes = graph.nodes.filter((n) => included.has(n.companyId));
  const edges = graph.edges.filter((e) => included.has(e.sourceCompanyId) && included.has(e.targetCompanyId));
  const neighborIds = [...included].filter((id) => !seed.has(id));

  const sub: LatestGraph = {
    ...graph,
    snapshotId: `${graph.snapshotId}:mine:${watched.slice().sort().join(",")}${includeNeighbors ? ":n" : ""}`,
    nodes,
    edges,
  };
  const model = layoutGalaxy(sub, capT);
  model.nodes.forEach((nd) => {
    nd.tier = seed.has(nd.id) ? 0 : Math.max(2, nd.tier);
  });
  return { model, missing, neighborIds };
}
