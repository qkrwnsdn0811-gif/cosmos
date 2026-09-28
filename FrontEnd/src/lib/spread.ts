import { PLANET_RADIUS_K } from "@/three/palette";
import type { SceneModel } from "./graph";

/**
 * 펼치기(spread) — 하단 도크의 슬라이더 하나로 은하를 세 단계로 본다 (Human Atlas 의 explode 와 같은 구조).
 *   0    은하  : 레이아웃 그대로 (나선 원반 + 주가 고도)
 *   0.5  평면  : 고도·원반 두께를 0 으로 눌러 평면에 놓고 반지름을 살짝 벌린다 — 성단이 갈라져 보이고 간선은 아직 살아 있다
 *   1    정렬  : 행성 크기(시가총액·연결 가중치) 내림차순 격자. 격자 위의 선은 관계가 아니라 잡음이라 간선은 사라진다
 * 좌표는 여기서 계산만 하고, 실제로 옮기는 것은 three/SpreadDriver 다 (live 버퍼 — morph.ts 규칙).
 * 모두 결정론적이라 같은 모델·같은 t 면 같은 자리다.
 */
export type SpreadStage = "galaxy" | "flat" | "sorted";

/** 평면 단계의 슬라이더 값 — 두 구간(은하→평면, 평면→정렬)의 경계 */
export const SPREAD_FLAT = 0.5;
/** 평면 단계에서 반지름을 벌리는 배율 — 카메라 프레이밍(Scene.poseFor)이 같은 배율만큼 물러난다 */
export const FLAT_SCALE = 1.12;
/** 격자의 가로/세로 칸 수 비 — 화면 종횡비(대개 1.6 안팎)에 맞춰, 격자가 화면을 고르게 채우게 한다 */
const GRID_ASPECT = 1.6;
/**
 * 칸 한 칸의 크기 = 가장 큰 node.size × 이 값. 가장 큰 행성의 지름(size × PLANET_RADIUS_K × 2)에 25% 여백을 둔 값이라,
 * 행성 크기를 키워도 격자에서 이웃과 겹치지 않는다. 카메라가 이 격자에 맞춰 들어오므로(three/Scene poseFor)
 * 여백을 줄이면 행성이 화면에서 더 커지고, 늘리면 사이가 넉넉해진다 — 둘의 균형점이다.
 */
const CELL_PER_SIZE = PLANET_RADIUS_K * 2 * 1.25;

export function spreadStage(t: number): SpreadStage {
  if (t < 0.25) return "galaxy";
  if (t < 0.75) return "flat";
  return "sorted";
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const smoothstep = (a: number, b: number, x: number) => {
  const k = clamp01((x - a) / (b - a));
  return k * k * (3 - 2 * k);
};
const easeInOut = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

/** 간선·화살촉·글자·기준 링이 남는 비율 — 평면을 지나 정렬로 가는 동안 사라진다 */
export function spreadEdgeAlpha(t: number) {
  return 1 - smoothstep(0.55, 0.85, t);
}

/** 카메라가 물러나야 하는 배율 — 평면 단계의 반지름 벌림을 프레임에 담는다 */
export function spreadRadiusScale(t: number) {
  return 1 + (FLAT_SCALE - 1) * smoothstep(0, SPREAD_FLAT, t);
}

/* ---- 정렬 격자 (모델당 한 번) ------------------------------------------ */
const GRID = new WeakMap<SceneModel, Float32Array>();
const EXTENT = new WeakMap<SceneModel, SortedExtent>();

/** 정렬 격자의 절반 크기 (월드 단위) — 카메라가 은하 반지름이 아니라 이 격자에 맞춰 들어온다 (three/Scene poseFor) */
export interface SortedExtent {
  halfW: number;
  halfH: number;
}

/** 칸 수·칸 크기 — 격자는 은하 반지름이 아니라 행성 크기가 정한다. 그래야 정렬해도 행성이 콩알만 해지지 않는다 */
function gridShape(model: SceneModel) {
  const n = model.nodes.length;
  const maxSize = model.nodes.reduce((m, nd) => Math.max(m, nd.size), 0.5);
  const cell = maxSize * CELL_PER_SIZE;
  const cols = Math.max(1, Math.ceil(Math.sqrt(Math.max(1, n) * GRID_ASPECT)));
  const rows = Math.ceil(Math.max(1, n) / cols);
  return { cols, rows, cell };
}

/** 정렬 격자가 차지하는 절반 크기. 카메라 프레이밍이 이 값을 쓴다 */
export function sortedExtent(model: SceneModel): SortedExtent {
  let e = EXTENT.get(model);
  if (e) return e;
  const { cols, rows, cell } = gridShape(model);
  e = { halfW: (cols * cell) / 2, halfH: (rows * cell) / 2 };
  EXTENT.set(model, e);
  return e;
}

/**
 * 크기 내림차순 격자 좌표 (노드 i 의 x·y·z 가 i*3..). y 는 0.
 * 첫 행(가장 큰 행성)은 -z 쪽, 위에서 보는 시점에서 화면 위다.
 */
function gridFor(model: SceneModel): Float32Array {
  let grid = GRID.get(model);
  if (grid) return grid;
  const n = model.nodes.length;
  grid = new Float32Array(n * 3);
  if (n === 0) {
    GRID.set(model, grid);
    return grid;
  }
  const order = model.nodes.map((_, i) => i).sort((a, b) => model.nodes[b].size - model.nodes[a].size || (model.nodes[a].id < model.nodes[b].id ? -1 : 1));
  const { cols, cell } = gridShape(model);
  const { halfW, halfH } = sortedExtent(model);
  order.forEach((i, k) => {
    const c = k % cols;
    const r = Math.floor(k / cols);
    grid![i * 3] = -halfW + cell * (c + 0.5);
    grid![i * 3 + 1] = 0;
    grid![i * 3 + 2] = -halfH + cell * (r + 0.5);
  });
  GRID.set(model, grid);
  return grid;
}

/**
 * 정렬 격자에서 카메라가 볼 자리 — 고른 기업이 있으면 그 기업의 칸, 없으면 첫 칸(행성이 가장 큰 기업)이다.
 * 격자는 크기 내림차순이라 첫 칸이 곧 "가장 큰 기업" 이고, 화면 왼쪽 위 모서리에 놓인다.
 */
export function sortedFocusPos(model: SceneModel, companyId: string | null | undefined): [number, number, number] {
  const n = model.nodes.length;
  if (n === 0) return [0, 0, 0];
  const grid = gridFor(model);
  let i = companyId ? model.nodes.findIndex((nd) => nd.id === companyId) : -1;
  if (i < 0) {
    i = 0;
    for (let k = 1; k < n; k += 1) if (model.nodes[k].size > model.nodes[i].size) i = k;
  }
  return [grid[i * 3], grid[i * 3 + 1], grid[i * 3 + 2]];
}

/**
 * 슬라이더 값 t 에서 모든 노드의 목표 좌표를 out 에 쓴다.
 * base 는 t=0 의 좌표(node.pos 에 주가 고도 y 를 더한 것) — 고도가 켜져 있어도 0 으로 돌아오면 정확히 그 자리다.
 */
export function spreadTargets(model: SceneModel, t: number, base: Float32Array, out: Float32Array) {
  const n = model.nodes.length;
  if (t <= 0) {
    out.set(base.subarray(0, n * 3));
    return;
  }
  const e1 = easeInOut(clamp01(t / SPREAD_FLAT));
  const k2 = clamp01((t - SPREAD_FLAT) / (1 - SPREAD_FLAT));
  const e2 = k2 > 0 ? easeInOut(k2) : 0;
  const grid = k2 > 0 ? gridFor(model) : null;
  for (let i = 0; i < n; i += 1) {
    const o = i * 3;
    const bx = base[o];
    const by = base[o + 1];
    const bz = base[o + 2];
    // 1) 은하 → 평면: 반지름을 살짝 벌리고 높이를 0 으로
    let x = bx + (bx * FLAT_SCALE - bx) * e1;
    let y = by + (0 - by) * e1;
    let z = bz + (bz * FLAT_SCALE - bz) * e1;
    // 2) 평면 → 정렬 격자
    if (grid) {
      x += (grid[o] - x) * e2;
      y += (grid[o + 1] - y) * e2;
      z += (grid[o + 2] - z) * e2;
    }
    out[o] = x;
    out[o + 1] = y;
    out[o + 2] = z;
  }
}
