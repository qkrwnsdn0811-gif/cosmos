import type { SceneModel, Vec3 } from "@/lib/graph";

/**
 * 노드의 "지금 그려지는" 위치·스케일 — 모델당 하나의 공유 버퍼.
 * `node.pos` 는 레이아웃 결과라 불변으로 두고(간선 from/to 가 참조), 워프 모프처럼 프레임마다 움직이는 값은
 * 여기에만 쓴다. 행성·칩·헤일로·격자가 모두 이 버퍼를 읽으므로 위치의 단일 소스가 된다.
 */
export interface LiveTransform {
  model: SceneModel;
  /** 노드 i 의 위치 (i*3 .. i*3+2) */
  pos: Float32Array;
  /** 노드 i 의 스케일 배율 (1 = 레이아웃 크기) */
  scale: Float32Array;
}

const LIVE = new WeakMap<SceneModel, LiveTransform>();

/** 모델의 live 버퍼. 없으면 node.pos 를 복사해 만든다 — 렌더 중에도 안전하게 호출할 수 있다 */
export function liveFor(model: SceneModel): LiveTransform {
  let live = LIVE.get(model);
  if (!live) {
    const n = model.nodes.length;
    const pos = new Float32Array(n * 3);
    const scale = new Float32Array(n).fill(1);
    model.nodes.forEach((node, i) => {
      pos[i * 3] = node.pos[0];
      pos[i * 3 + 1] = node.pos[1];
      pos[i * 3 + 2] = node.pos[2];
    });
    live = { model, pos, scale };
    LIVE.set(model, live);
  }
  return live;
}

/**
 * 행성 드래그 — 이 화면(세션)에서만 유효한 임시 배치.
 * live.pos 에만 쓰고 node.pos 는 그대로 두므로, 새로고침해서 모델을 다시 만들면 liveFor 가 레이아웃 좌표로 초기화한다(= 원래 자리).
 * id 는 지금 끌고 있는 노드(카메라 컨트롤을 잠그는 신호), version 은 "위치가 바뀌었다" 는 카운터,
 * dirty 는 이번 드래그에서 움직인 노드 id 다. 소비자(간선·화살촉·격자)가 여럿이라 dirty 는 드래그를 시작할 때만 비운다 —
 * 한 소비자가 처리 후 비우면 나머지가 그 변화를 놓친다.
 * blockClick 은 드래그로 끝난 pointerup 뒤에 브라우저가 한 번 더 보내는 click(워프·핀·간선 선택·빈 곳 클릭)을 삼키는 표식으로,
 * 그 click 이 지나간 다음 프레임에 풀린다.
 */
export const dragState = { id: null as string | null, version: 0, dirty: new Set<string>(), blockClick: false };

/** 워프 모프 계획 — 원통 좌표로 보간할 출발·도착 상태 */
export interface MorphPlan {
  src: SceneModel;
  dst: SceneModel;
  /** 진행도 0~1 */
  t: number;
  n: number;
  /** 커밋~착지 실제 길이(초) — 고스트가 자기 수명(초)을 진행도로 환산하는 데 쓴다 */
  dur: number;
  rA: Float32Array;
  thA: Float32Array;
  yA: Float32Array;
  rB: Float32Array;
  thB: Float32Array;
  yB: Float32Array;
  dth: Float32Array;
  delay: Float32Array;
  sizeRatio: Float32Array;
  spawn: Uint8Array;
  ghosts: { pos: Vec3; dir: Vec3; color: string; radius: number }[];
}

/**
 * 모프 진행 상태 — Director 가 쓰고 행성·칩·빔이 읽는다.
 * plan 이 null 이면 모프 중이 아니다(현재 팝인 폴백). fade/reveal 은 빔 게이트, landedAt 은 착지 시각(ms).
 *
 * 워프 타이밍 (Scene.tsx Director 가 주인, 초 단위 el = now - warpStartedAt):
 *   0 ~ 0.6   가속·스트릭. fade 1→0 (0.25s 안에 빔·구슬 완전 소멸), reveal 0, 칩·배지 페이드아웃
 *   0.6       커밋 — focusId 교체, planMorph 생성, 카메라·controls.target 을 피벗 차이만큼 평행이동(컷 숨김)
 *   0.6 ~ 1.7 낙하 — sampleMorph 가 live 버퍼를 원통 좌표로 채운다. 고스트는 앞 0.45s 안에 이탈·소멸
 *   1.7       착지 — plan=null, landedAt 기록. 이후 0.6s 동안 reveal 0→1 로 빔이 안쪽에서 바깥으로 자란다
 */
export const morphState = { plan: null as MorphPlan | null, fade: 1, reveal: 1, landedAt: 0 };

const TAU = Math.PI * 2;
/** 지연 상한 — (1 - delay) 가 분모라 1 에 붙으면 낙하가 순간이동이 된다 */
const MAX_DELAY = 0.5;
/** 기업 중심 뷰로 갈 때 depth 당 출발 지연 (안쪽 궤도가 먼저 도착한다) */
const DEPTH_DELAY = 0.12;
/** 은하로 돌아갈 때의 지연 — 작은(가벼운) 행성일수록 늦게 도착한다 */
const SIZE_DELAY = 0.3;
/** 목적지에만 있는 노드가 스폰되는 방위 배율 — 제자리 바깥에서 안으로 들어온다 */
const SPAWN_OUT = 2.6;
/** 스폰 노드가 다 자라는 진행도 */
const SPAWN_IN = 0.5;
/** 나선 낙하의 옆흔들림(rad) — 직선으로 빨려 들어가지 않고 휘어 들어간다 */
const SWIRL = 0.5;
/** easeOutBack 되튐 세기 — 궤도에 살짝 튕겨 멈춘다 */
const BACK = 1.12;

const easeOutBack = (x: number) => {
  const u = x - 1;
  return 1 + (BACK + 1) * u * u * u + BACK * u * u;
};
const smooth01 = (x: number) => x * x * (3 - 2 * x);
/** 최단호로 접은 각도 차 (-π..π) */
function wrapPi(a: number) {
  let v = (a + Math.PI) % TAU;
  if (v < 0) v += TAU;
  return v - Math.PI;
}

/**
 * 두 배치 사이의 모프 계획을 만든다.
 * - 공통 노드: 출발점을 `src.pos - pivotSrc + pivotDst` 로 옮겨(= 피벗을 겹친 좌표계) 원통 좌표로 보간한다.
 *   그래서 은하에서 목표의 왼쪽에 있던 이웃은 기업 중심 뷰에서도 왼쪽에서 들어온다.
 * - 목적지에만 있는 노드: 제자리의 SPAWN_OUT 배 바깥에서 작게 태어나 같은 궤적으로 떨어진다.
 * - 출발지에만 있는 노드: ghosts — GhostNodes 가 자기 방위 바깥으로 튕겨 소멸시킨다.
 * `src`/`dst` 의 node.pos 는 읽기만 한다 (불변).
 */
export function planMorph(src: SceneModel, dst: SceneModel, pivotSrc: Vec3, pivotDst: Vec3, dur = 1): MorphPlan {
  const n = dst.nodes.length;
  const rA = new Float32Array(n);
  const thA = new Float32Array(n);
  const yA = new Float32Array(n);
  const rB = new Float32Array(n);
  const thB = new Float32Array(n);
  const yB = new Float32Array(n);
  const dth = new Float32Array(n);
  const delay = new Float32Array(n);
  const sizeRatio = new Float32Array(n);
  const spawn = new Uint8Array(n);
  let maxSize = 1e-3;
  dst.nodes.forEach((nd) => {
    maxSize = Math.max(maxSize, nd.size);
  });

  dst.nodes.forEach((nd, i) => {
    const s = src.nodeById.get(nd.id);
    let fx: number;
    let fy: number;
    let fz: number;
    if (s) {
      fx = s.pos[0] - pivotSrc[0] + pivotDst[0];
      fy = s.pos[1] - pivotSrc[1] + pivotDst[1];
      fz = s.pos[2] - pivotSrc[2] + pivotDst[2];
      // 같은 기업이 뷰마다 다른 크기(기업 중심의 중심 3.4 등)라 커밋 프레임에서 크기가 점프하지 않도록 비율에서 시작한다
      sizeRatio[i] = nd.size > 1e-3 ? s.size / nd.size : 1;
    } else {
      spawn[i] = 1;
      fx = nd.pos[0] * SPAWN_OUT;
      fy = nd.pos[1] * SPAWN_OUT;
      fz = nd.pos[2] * SPAWN_OUT;
      sizeRatio[i] = 1;
    }
    rA[i] = Math.hypot(fx, fz);
    yA[i] = fy;
    rB[i] = Math.hypot(nd.pos[0], nd.pos[2]);
    yB[i] = nd.pos[1];
    thB[i] = Math.atan2(nd.pos[2], nd.pos[0]);
    // 축 위에서 출발하는 노드는 방위가 없다 — 도착 방위로 맞춰 반경만 자라게 한다
    thA[i] = rA[i] < 1e-3 ? thB[i] : Math.atan2(fz, fx);
    dth[i] = wrapPi(thB[i] - thA[i]);
    delay[i] = Math.min(MAX_DELAY, dst.kind === "system" ? DEPTH_DELAY * nd.depth : SIZE_DELAY * (1 - nd.size / maxSize));
  });

  const ghosts: MorphPlan["ghosts"] = [];
  src.nodes.forEach((s) => {
    if (dst.nodeById.has(s.id)) return;
    const pos: Vec3 = [s.pos[0] - pivotSrc[0] + pivotDst[0], s.pos[1] - pivotSrc[1] + pivotDst[1], s.pos[2] - pivotSrc[2] + pivotDst[2]];
    const len = Math.hypot(pos[0], pos[1], pos[2]);
    ghosts.push({ pos, dir: len < 1e-3 ? [0, 1, 0] : [pos[0] / len, pos[1] / len, pos[2] / len], color: s.color, radius: s.size * 1.3 });
  });

  return { src, dst, t: 0, n, dur, rA, thA, yA, rB, thB, yB, dth, delay, sizeRatio, spawn, ghosts };
}

/**
 * 진행도 t(0~1) 를 목적지 모델의 live 버퍼에 쓴다. `pos`/`scale` 만 건드리고 node.pos 는 읽지 않는다
 * (착지 프레임만 예외로 레이아웃 좌표를 그대로 복사해, 워프를 왕복해도 오차가 쌓이지 않게 한다).
 */
export function sampleMorph(plan: MorphPlan, t: number, live: LiveTransform) {
  const { pos, scale } = live;
  const n = Math.min(plan.n, scale.length);
  for (let i = 0; i < n; i += 1) {
    const d = plan.delay[i];
    const ti = Math.min(1, Math.max(0, (t - d) / (1 - d)));
    if (ti >= 1) {
      const p = plan.dst.nodes[i].pos;
      pos[i * 3] = p[0];
      pos[i * 3 + 1] = p[1];
      pos[i * 3 + 2] = p[2];
      scale[i] = 1;
      continue;
    }
    const k = easeOutBack(ti);
    const r = plan.rA[i] + (plan.rB[i] - plan.rA[i]) * k;
    // 회전 방향이 없으면(반경만 변하는 낙하) 한쪽으로 감아 들어가게 부호를 +1 로 둔다
    const swirl = plan.dth[i] < 0 ? -SWIRL : SWIRL;
    const th = plan.thA[i] + plan.dth[i] * k + swirl * Math.sin(Math.PI * k);
    pos[i * 3] = Math.cos(th) * r;
    pos[i * 3 + 1] = plan.yA[i] + (plan.yB[i] - plan.yA[i]) * smooth01(ti);
    pos[i * 3 + 2] = Math.sin(th) * r;
    scale[i] = plan.spawn[i] ? smooth01(Math.min(1, ti / SPAWN_IN)) : plan.sizeRatio[i] + (1 - plan.sizeRatio[i]) * k;
  }
}
