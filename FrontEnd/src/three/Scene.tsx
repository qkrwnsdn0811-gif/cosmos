import { useEffect, useLayoutEffect, useMemo, useRef, type MutableRefObject } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { AdaptiveDpr, OrbitControls } from "@react-three/drei";
import { Bloom, ChromaticAberration, EffectComposer, Vignette } from "@react-three/postprocessing";
import * as THREE from "three";
import { ORBIT, type SceneEdge, type SceneModel, type Vec3 } from "@/lib/graph";
import type { CameraPresetId } from "@/lib/guide";
import { hoverPath } from "@/lib/path";
import { sortedExtent, sortedFocusPos, spreadRadiusScale, spreadStage } from "@/lib/spread";
import { surpriseSet } from "@/lib/surprise";
import { hashString } from "@/lib/rng";
import { neighborRoles } from "@/lib/roles";
import { useGalaxy } from "@/store/galaxy";
import AltitudeDriver from "./AltitudeDriver";
import Backdrop from "./Backdrop";
import CompanyNodes from "./CompanyNodes";
import EdgeArrows from "./EdgeArrows";
import EdgeLabels from "./EdgeLabels";
import GalaxyDust from "./GalaxyDust";
import GhostNodes from "./GhostNodes";
import OrbitKeys from "./OrbitKeys";
import ReferenceRings from "./ReferenceRings";
import RelationLines from "./RelationLines";
import RoleRings from "./RoleRings";
import SpreadDriver from "./SpreadDriver";
import TourAnchor from "./TourAnchor";
import Warp from "./Warp";
import { targetY } from "./altitude";
import { cameraBusy } from "./cameraState";
import { markSceneReady, resetSceneReady } from "./sceneReady";
import { computeEmphasis } from "./emphasis";
import { bindLogoAtlasRenderer } from "./logoAtlas";
import { dragState, liveFor, morphState, planMorph, sampleMorph } from "./morph";
import { LAB, PLANET_RADIUS_K } from "./toon";

type Pose = { pos: [number, number, number]; tgt: [number, number, number] };
const FALLBACK_POSE: Record<"galaxy" | "system", Pose> = {
  galaxy: { pos: [0, 90, 230], tgt: [0, 0, 0] },
  system: { pos: [0, 40, 118], tgt: [0, -2, 0] },
};
/**
 * 프리셋별 카메라 높이(정규화한 y 성분). 궤도 = 지금까지의 비스듬한 자세, 위에서 = 거의 수직 부감(극각 하한 0.03rad 안),
 * 정면에서 = 원반을 옆에서(극각 상한 0.62π 안) — 주가 고도의 높이 차가 그대로 보인다
 */
const PRESET_ELEV: Record<"galaxy" | "system", Record<CameraPresetId, number>> = {
  galaxy: { orbit: 0.5, top: 0.965, front: 0.08 },
  system: { orbit: 0.34, top: 0.965, front: 0.1 },
};
/**
 * 프리셋별 거리 배율 (r / fit 기준). 궤도는 원반이 세로로 눌려 보여 바깥 반지름보다 가까이서도 들어온다 —
 * 첫 화면이 멀어 보인다는 지적에 0.72 → 0.56 으로 당겼다가, 이번엔 너무 가깝다고 해 0.66 으로 조금 물렸다
 * (간격을 넓히며 커진 원반만큼 되물러나지 않도록 상쇄한 값이라 여전히 예전보다는 가깝다).
 * 위에서는 눌림이 없어 1 이 필요하고, 정면에서는 세로가 아니라 가로가 먼저 잘리므로 종횡비만큼 가까워질 수 있다
 * (세로로 긴 화면은 fit 이 이미 종횡비를 반영한다).
 * 기업 중심 뷰의 궤도는 1.0 이던 때 착지 확대가 세다는 지적(행성이 오밀조밀)에 1.12 로 물렸다 — 궤도 간격(graph.ts ORBIT·RING_SPACING)을
 * 벌린 것과 합쳐 착지 화면의 행성 지름이 예전의 2/3 쯤이 된다
 */
function distScale(kind: "galaxy" | "system", preset: CameraPresetId, aspect: number) {
  if (preset === "top") return kind === "galaxy" ? 1.02 : 1.1;
  if (preset === "front") return aspect >= 1 ? 1.1 / aspect : 1.1;
  return kind === "galaxy" ? 0.66 : 1.12;
}

/**
 * 정렬 단계에서 한 화면에 담는 격자 비율 — 1 이면 격자 전체다. 0.55(절반)로 당겼더니 격자가 잘려 "가지런한 전체" 가
 * 보이지 않는다는 지적이 있어 격자 전체 + 여백(1.18)으로 되돌렸다. 딱 1 로 두면 30° 가까운 화각의 원근 때문에
 * 앞쪽 줄이 화면 밖으로 밀린다. 시선은 이 시야 안에서만 기업 쪽으로 옮기므로(clampIn),
 * 격자가 통째로 들어오는 지금은 자연히 한가운데에 머문다 — 더 당기면 그만큼 고른 기업 쪽으로 따라간다.
 */
const SORTED_VIEW = 1.18;

/** 격자 안에서 시선이 갈 수 있는 범위로 묶는다 — 시야가 격자보다 크면 한가운데(0)로 모인다 */
function clampIn(v: number, half: number, view: number) {
  const room = Math.max(0, half - view);
  return Math.min(room, Math.max(-room, v));
}

/** 모델의 바깥 반지름이 화면에 들어오도록 카메라 자세를 만든다 (fov 52 기준)
 * @param panelBias 0~1 — PathGalaxyPage 전용. 우측에 고정폭 패널이 겹쳐 올라오므로, 시선(tgt)을 그만큼
 * 오른쪽으로 밀어 실제 내용이 화면 왼쪽~가운데에 몰리게 하고, 잘리지 않도록 r 도 살짝 벌린다. 0(기본)이면 기존과 동일하다
 */
function poseFor(model: SceneModel | null, kind: "galaxy" | "system", aspect = 1.6, preset: CameraPresetId = "orbit", panelBias = 0): Pose {
  if (!model || !model.nodes.length) return FALLBACK_POSE[kind];
  let r = 10;
  // 주가 고도가 켜져 있으면 행성이 원반 위·아래로 최대 ±ALT_SPAN 만큼 떠 있다 — 그 높이도 프레임에 들어와야 한다
  const alt = useGalaxy.getState();
  const useAltitude = kind === "galaxy" && alt.altitudeOn;
  // 정렬 단계는 은하가 아니라 격자를 본다 — 은하 반지름으로 잡으면 격자가 화면 한가운데 작게 남아 행성이 콩알만 해진다.
  // 화면 반쪽 크기는 종횡비 a 에서 (r·a, r) [a ≥ 1] 또는 (r, r/a) [a < 1] 이므로 그 역으로 필요한 r 을 구한다.
  // 격자 전체가 아니라 그 절반쯤(SORTED_VIEW)을 보고, 시선은 고른 기업(없으면 가장 큰 기업)의 칸에 둔다 — 순위의 어디를 보고 있는지가 분명해진다
  let focus: [number, number, number] | null = null;
  const sorted = kind === "galaxy" && spreadStage(alt.spread) === "sorted";
  if (sorted) {
    const { halfW, halfH } = sortedExtent(model);
    const a = Math.max(0.2, aspect);
    r = (a >= 1 ? Math.max(halfH, halfW / a) : Math.max(halfW, halfH * a)) * SORTED_VIEW;
    // 이 거리에서 화면에 담기는 반쪽 크기 — 시선을 이만큼 안쪽으로 묶어 두면 격자가 늘 화면을 채운다
    const viewW = a >= 1 ? r * a : r;
    const viewH = a >= 1 ? r : r / a;
    const [fx, , fz] = sortedFocusPos(model, alt.selectedId);
    focus = [clampIn(fx, halfW, viewW), 0, clampIn(fz, halfH, viewH)];
  } else {
    model.nodes.forEach((n) => {
      // 은하도 이제 납작한 원반이라 두 뷰 모두 바닥면(x·z) 기준으로 바깥 반지름을 잡는다
      const planar = Math.hypot(n.pos[0], n.pos[2]) + n.size * PLANET_RADIUS_K;
      // 30° 부감에서 높이 1 은 화면 세로를 평면 1 보다 약 1.7 배(cos30/sin30) 많이 먹는다 — 여유를 봐 2 배로 잡는다
      const h = (Math.abs(useAltitude ? targetY(n, alt.altitudeWindow) : n.pos[1]) + n.size * PLANET_RADIUS_K) * 2;
      r = Math.max(r, useAltitude ? Math.hypot(planar, h) : planar);
    });
    // 펼치기의 평면 단계는 반지름을 살짝 벌린다(lib/spread FLAT_SCALE) — 그만큼 물러나야 바깥 행성이 잘리지 않는다
    if (kind === "galaxy") r *= spreadRadiusScale(alt.spread);
    // 기업 중심 뷰에서 주변 관계가 하나도 없으면(중심 기업 혼자) r 이 그 행성 자신의 반지름 정도(≈10)로만 잡혀
    // 카메라가 코앞까지 바짝 붙는다 — 관계가 하나라도 있는 가장 좁은 뷰(depth 1 궤도, ORBIT[1])만큼은 물러나
    // 화면 비율을 지키면서도, 그 안에 행성 하나만 외로이 떠 있는 모습으로 "관계 없음"이 보이게 한다
    if (kind === "system") r = Math.max(r, ORBIT[1]);
    // 우측 패널 자리를 비워 둔다 — 시선을 오른쪽으로 밀 것이므로 그만큼 안 잘리게 여유를 둔다
    if (panelBias) r *= 1 + panelBias * 0.6;
  }
  // 세로로 긴 화면은 가로가 먼저 잘리므로 종횡비만큼 물러난다
  const fit = Math.tan((52 * Math.PI) / 360) * Math.min(1, aspect);
  const dist = (r / fit) * distScale(kind, preset, aspect);
  // 궤도 프리셋의 은하는 약 30° 위에서 비스듬히 — 원반이 타원으로 보이면서 핵의 허브가 다른 행성에 가리지 않는다
  const elev = PRESET_ELEV[kind][preset];
  const biasX = panelBias && !sorted ? r * panelBias * 0.5 : 0;
  const tgt: [number, number, number] = focus ?? [biasX, kind === "galaxy" ? 0 : -2, 0];
  return { pos: [tgt[0], tgt[1] + dist * elev, tgt[2] + dist * Math.sqrt(1 - elev * elev)], tgt };
}
/**
 * 워프 타이밍 (초, warpStartedAt 기준) — morph.ts 의 표와 같은 것을 여기서 실행한다.
 *   0 ~ 0.6   가속·스트릭, 빔 페이드아웃(morphState.fade 1→0, 0.25s), 칩·배지 페이드아웃
 *   0.6       커밋 — focusId 교체 + planMorph + 카메라·시선 피벗 평행이동(순간이동 컷 없음)
 *   0.6 ~ 1.7 나선 낙하(sampleMorph) · 고스트 이탈(앞 0.45s) · 격자 재settle
 *   1.7       착지 — plan=null, landedAt 기록 → 이후 0.6s 동안 빔 리빌
 */
const WARP_COMMIT = 0.6;
const WARP_END = 1.7;
/** 워프 리빌 길이(ms) — 착지 후 이 시간 동안 빔이 안쪽에서 바깥으로 자란다 */
const REVEAL_MS = 600;
/** 인트로 비행 길이(초) */
const INTRO_S = 2.4;
/** 인트로 워밍업 상한(ms) — 프레임이 안정되지 않아도 이 시간 뒤에는 비행을 시작한다 */
const WARMUP_TIMEOUT_MS = 2500;
/** 워밍업이 끝났다고 보는 프레임 간격(초) — 직전 프레임(컴파일·업로드를 치른 프레임)이 이 아래로 내려오면 매끄럽게 날 수 있다 */
const WARM_FRAME_S = 0.05;
/** 워밍업에서 최소로 지켜보는 프레임 수 — 첫 프레임은 커밋, 둘째가 첫 드로우(컴파일), 셋째부터 간격을 판정한다 */
const WARM_MIN_FRAMES = 3;
const ORIGIN: Vec3 = [0, 0, 0];
const SHIFT = new THREE.Vector3();
const PIVOT = new THREE.Vector3();
const easeOutCubic = (x: number) => 1 - Math.pow(1 - x, 3);
const easeInQuad = (x: number) => x * x;
const smoothstep = (a: number, b: number, x: number) => {
  const t = THREE.MathUtils.clamp((x - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};

interface Anim {
  t: number;
  dur: number;
  fromPos: THREE.Vector3;
  fromTgt: THREE.Vector3;
  toPos: THREE.Vector3;
  toTgt: THREE.Vector3;
}

/** 카메라 연출 + 워프 타이밍. 스토어는 getState 로 읽어 리렌더를 만들지 않는다. */
function Director({
  warpRef,
  galaxy,
  system,
  next,
  panelBias = 0,
}: {
  warpRef: MutableRefObject<number>;
  galaxy: SceneModel | null;
  system: SceneModel | null;
  next: SceneModel | null;
  /** PathGalaxyPage 전용 우측 패널 여백 — poseFor 로 그대로 흘려보낸다 */
  panelBias?: number;
}) {
  // 이 Director 안의 poseFor 호출 전부를 같은 panelBias 로 통일하는 헬퍼 — 은하·기업 중심 뷰는 0 을 넘겨 기존과 동일하다
  const pf = (model: SceneModel | null, kind: "galaxy" | "system", aspect: number, preset: CameraPresetId) => poseFor(model, kind, aspect, preset, panelBias);
  const camera = useThree((s) => s.camera);
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update: () => void; enabled: boolean } | null;
  const rig = useRef({
    lastPhase: "" as string,
    anim: null as Anim | null,
    warpDir: new THREE.Vector3(),
    warpAim: new THREE.Vector3(),
    speed: 0,
    /** 마지막으로 반영한 카메라 프리셋 선택 횟수 — 마운트 시점 값으로 시작해 첫 프레임에 연출이 튀지 않게 */
    presetNonce: useGalaxy.getState().cameraPresetNonce,
    /** 펼치기 슬라이더가 평면(0.5)을 넘어 있는지 — 넘는 순간 위에서 보는 시점으로, 돌아오는 순간 이전 시점으로 */
    spreadAbove: false,
    /** 펼치기가 정렬 단계(격자)에 들어와 있는지 — 프레이밍 기준이 은하 반지름 ↔ 격자로 바뀌는 경계 */
    spreadSorted: false,
    /** 정렬 격자에서 지금 시선을 둔 기업 (빈 문자열 = 가장 큰 기업) */
    spreadFocus: "",
    presetBeforeSpread: null as CameraPresetId | null,
    /** 인트로 워밍업 — 모델이 마운트된 뒤 지난 프레임 수와 시작 시각. 프레임 간격이 안정되면(또는 상한에 닿으면) 비행을 시작한다 */
    intro: null as { pose: Pose; frames: number; startedAt: number } | null,
  }).current;

  // 항상 "지금 카메라가 있는 곳"에서 시작한다 — 워프 커밋도 순간이동 없이 이어 붙인다
  const startAnim = (to: Pose, dur: number) => {
    const tgt = controls ? controls.target : new THREE.Vector3();
    rig.anim = {
      t: 0,
      dur,
      fromPos: camera.position.clone(),
      fromTgt: tgt.clone(),
      toPos: new THREE.Vector3().fromArray(to.pos),
      toTgt: new THREE.Vector3().fromArray(to.tgt),
    };
  };

  // 워프 도중에 씬이 사라지면(라우트 이동) 계획이 남아 다음 마운트의 빔·피킹을 잠근다 — 모프 상태는 Director 수명에 묶는다.
  // cameraBusy 도 마찬가지다: 이 값을 쓰는 건 Director 뿐인데 읽는 쪽에는 연출 카메라가 없는 미니 씬의 드래그도 있어,
  // 연출 중에 떠나면 true 로 굳어 그쪽 입력이 영영 막힌다
  useEffect(
    () => () => {
      morphState.plan = null;
      morphState.fade = 1;
      morphState.reveal = 1;
      cameraBusy.anim = false;
    },
    [],
  );

  // 뷰가 바뀌지 않았는데 모델(산업 필터·기업 그래프 로딩)이 갱신되면 카메라를 다시 맞춘다
  const fitted = useRef<SceneModel | null>(null);
  // 은하 모델이 처음 준비되면 바깥에서 안으로 날아드는 인트로
  const introDone = useRef(false);
  // 레이아웃 이펙트인 이유: 카메라를 시작 자세에 세우는 일이 이 커밋의 첫 R3F 프레임보다 먼저 일어나야 한다 (useEffect 는 한 프레임 뒤에 돌 수 있다)
  useLayoutEffect(() => {
    if (introDone.current || !galaxy) return;
    introDone.current = true;
    fitted.current = galaxy;
    const pose = pf(galaxy, "galaxy", (camera as THREE.PerspectiveCamera).aspect, useGalaxy.getState().cameraPreset);
    camera.position.set(...pose.pos).multiplyScalar(1.7);
    if (controls) {
      controls.target.fromArray(pose.tgt);
      controls.update();
    }
    rig.lastPhase = "galaxy";
    /*
     * 워밍업. 모델이 마운트된 이 프레임에 바로 비행을 시작하면 첫 드로우의 셰이더 컴파일·지오메트리·텍스처 업로드(실측 370~490ms)가
     * 비행 첫 장면의 끊김으로 보였다 (2026-09-18). 카메라를 시작 자세에 세워 둔 채 그 첫 드로우를 먼저 치르고, 프레임 간격이
     * WARM_FRAME_S 아래로 안정되면(useFrame 의 rig.intro 처리) 비행을 시작한다. 로딩 오버레이(GalaxyPage)는 markSceneReady 까지 남아 그 사이를 덮는다.
     * gl.compileAsync 로 미리 컴파일하는 방식은 쓰지 않는다 — Windows 의 ANGLE(D3D11)은 실제 첫 드로우에서 HLSL 을 다시 컴파일하므로
     * 숨긴 채 컴파일한 뒤 공개해도 공개 프레임에 485ms 가 그대로 남았다. 프레임 시간으로 판정하는 쪽이 플랫폼을 가리지 않는다.
     * 이 이펙트는 galaxy 가 바뀔 때마다 다시 돌지만 introDone 이 막는다
     */
    rig.intro = { pose, frames: 0, startedAt: performance.now() };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [galaxy, camera]);

  /* eslint-disable react-hooks/immutability -- The R3F frame callback controls the external Three.js camera and orbit controls. */
  useFrame((_, delta) => {
    const dt = Math.min(delta, 0.05);
    const s = useGalaxy.getState();
    const now = performance.now();
    let intensity = 0;

    if (s.phase === "warp") {
      const el = (now - s.warpStartedAt) / 1000;
      // 터널 피크를 커밋 직후로 당겨 배치 교체를 덮고, 1.15s 에는 걷혀 낙하 후반이 맑게 보인다
      intensity = smoothstep(0, 0.22, el) * (1 - smoothstep(WARP_COMMIT + 0.1, WARP_COMMIT + 0.55, el)) * 0.85;
      if (el < WARP_COMMIT) {
        if (rig.speed === 0) {
          // 기업 → 기업 워프도 지금 보이는 뷰(system) 안의 목표를 향해 가속한다
          const aim = s.warpTarget ? (s.focusId === null ? galaxy : system)?.nodeById.get(s.warpTarget)?.pos : null;
          if (aim) rig.warpAim.fromArray(aim);
          else rig.warpAim.set(0, 0, 0);
          rig.warpDir.copy(rig.warpAim).sub(camera.position).normalize();
        }
        rig.speed = THREE.MathUtils.lerp(rig.speed, 150, dt * 3.2);
        camera.position.addScaledVector(rig.warpDir, rig.speed * dt * easeInQuad(el / WARP_COMMIT));
        if (controls) {
          controls.target.lerp(rig.warpAim, dt * 3);
          controls.update();
        }
        // 빔·구슬은 가속 0.25s 안에 완전히 사라진다 (착지 후 리빌로 다시 자란다)
        morphState.fade = 1 - smoothstep(0, 0.25, el);
        morphState.reveal = 0;
      } else if (!s.warpCommitted) {
        const prevFocus = s.focusId;
        s.commitWarp();
        const src = prevFocus ? system : galaxy;
        // 목적지 배치가 아직 안 왔으면(next null) plan 없이 기존 팝인·재프레이밍으로 폴백한다
        const dst = s.warpTarget ? next : galaxy;
        // 피벗 = 두 배치에서 같은 자리가 되는 점. 은하→기업: 목표의 은하 좌표 → 원점 / 복귀: 원점 → 떠나온 기업의 은하 좌표
        const pivotSrc = (s.warpTarget ? src?.nodeById.get(s.warpTarget)?.pos : undefined) ?? ORIGIN;
        const pivotDst = (s.warpTarget || !prevFocus ? undefined : galaxy?.nodeById.get(prevFocus)?.pos) ?? ORIGIN;
        morphState.plan = src && dst && src !== dst ? planMorph(src, dst, pivotSrc, pivotDst, WARP_END - WARP_COMMIT) : null;
        morphState.fade = 1;
        // 목표 행성의 화면 위치가 그대로이도록 카메라·시선도 같은 양만큼 평행이동한다 (순간이동 컷 제거)
        SHIFT.fromArray(pivotDst).sub(PIVOT.fromArray(pivotSrc));
        camera.position.add(SHIFT);
        if (controls) controls.target.add(SHIFT);
        const kind: "galaxy" | "system" = s.warpTarget ? "system" : "galaxy";
        fitted.current = dst;
        startAnim(pf(dst ?? (s.warpTarget ? system : galaxy), kind, (camera as THREE.PerspectiveCamera).aspect, s.cameraPreset), WARP_END - WARP_COMMIT - 0.05);
        // 착지 자세가 이미 지금 프리셋이다 — 워프 중 고른 프리셋을 착지 뒤 한 번 더 연출하지 않게 여기서 맞춘다
        rig.presetNonce = s.cameraPresetNonce;
        rig.speed = 0;
        rig.lastPhase = "warp-committed";
      }
      const plan = morphState.plan;
      if (plan && el >= WARP_COMMIT) {
        plan.t = Math.min(1, (el - WARP_COMMIT) / (WARP_END - WARP_COMMIT));
        sampleMorph(plan, plan.t, liveFor(plan.dst));
      }
      if (el >= WARP_END) {
        // plan 은 여기서 지우지 않는다 — 이 프레임에 sampleMorph(t=1) 로 착지 좌표를 막 썼고,
        // Director 뒤에 도는 헤일로(haloPos.needsUpdate)·격자(writeWells)가 plan 을 보고 마지막 값을 올리기 때문.
        // 정리는 다음 프레임의 비-워프 분기가 한다 (한 프레임 = 16ms 라 연출 차이는 없다).
        morphState.landedAt = now;
        s.completeWarp();
        rig.lastPhase = s.warpTarget ? "system" : "galaxy";
      }
    } else {
      rig.speed = 0;
      // 착지 프레임이 남겨 둔 계획을 여기서 정리한다 (워프가 중간에 끊겨 phase 가 빠져나온 경우의 안전장치이기도 하다)
      morphState.plan = null;
      morphState.fade = 1;
      // 착지 후 0.6s 동안 빔이 자란다 (landedAt 0 = 아직 워프한 적 없음 → 마운트 램프에만 맡긴다)
      morphState.reveal = morphState.landedAt === 0 ? 1 : Math.min(1, (now - morphState.landedAt) / REVEAL_MS);
      const aspect = (camera as THREE.PerspectiveCamera).aspect;
      if (rig.lastPhase !== s.phase && rig.lastPhase !== "warp-committed") {
        startAnim(s.phase === "system" ? pf(system, "system", aspect, s.cameraPreset) : pf(galaxy, "galaxy", aspect, s.cameraPreset), 1.4);
      }
      if (rig.lastPhase !== s.phase) rig.lastPhase = s.phase;
      const model = s.phase === "system" ? system : galaxy;
      const kind: "galaxy" | "system" = s.phase === "system" ? "system" : "galaxy";
      // 펼치기가 평면을 넘으면 격자는 위에서 봐야 읽힌다 — 프리셋을 '위에서' 로 바꾸고, 다시 내려오면 그때의 프리셋으로 돌려놓는다 (은하 뷰만).
      // 사용자가 그 사이 다른 프리셋을 골랐으면(top 이 아니면) 그 선택을 존중해 되돌리지 않는다
      const above = kind === "galaxy" && s.spread >= 0.5;
      if (above !== rig.spreadAbove) {
        rig.spreadAbove = above;
        if (above) {
          rig.presetBeforeSpread = s.cameraPreset;
          if (s.cameraPreset !== "top") s.setCameraPreset("top");
        } else if (rig.presetBeforeSpread && rig.presetBeforeSpread !== "top" && s.cameraPreset === "top") {
          s.setCameraPreset(rig.presetBeforeSpread);
          rig.presetBeforeSpread = null;
        }
      }
      // 정렬 단계는 프레이밍 기준이 은하 반지름에서 격자로 바뀐다 — 경계를 넘을 때 한 번 다시 맞춰 준다
      // (프리셋 전환은 0.5 에서 일어나므로 그 뒤 슬라이더를 더 미는 이 구간에는 다시 맞출 계기가 없다)
      const onGrid = kind === "galaxy" && spreadStage(s.spread) === "sorted";
      // 정렬 중에는 고른 기업이 바뀔 때도 그 칸으로 시선을 옮긴다 (정렬 상태에서 행성을 눌렀을 때 포함)
      const gridFocus = onGrid ? (s.selectedId ?? "") : "";
      if (onGrid !== rig.spreadSorted || gridFocus !== rig.spreadFocus) {
        rig.spreadSorted = onGrid;
        rig.spreadFocus = gridFocus;
        if (model && introDone.current) startAnim(pf(model, kind, aspect, onGrid ? "top" : s.cameraPreset), 1.0);
      }
      // 카메라 프리셋을 골랐다 (같은 것을 다시 눌러도 횟수가 오른다) — 진행 중 연출이 있어도 그 자리에서 새 자세로 이어 간다
      if (s.cameraPresetNonce !== rig.presetNonce) {
        rig.presetNonce = s.cameraPresetNonce;
        if (model && introDone.current) {
          fitted.current = model;
          // 워밍업 중에 고른 프리셋은 대기 중인 인트로 비행의 목표를 바꾼다 — 여기서 startAnim 을 걸면 아래 인트로 시작이 처음 자세로 덮어써 선택이 사라진다
          if (rig.intro) rig.intro.pose = pf(model, kind, aspect, s.cameraPreset);
          else startAnim(pf(model, kind, aspect, s.cameraPreset), 1.2);
        }
      }
      // 워프 중에 그래프가 도착했거나 필터로 모델이 바뀐 경우: 진행 중인 애니메이션이 끝난 뒤 현재 모델에 다시 맞춘다
      if (model && fitted.current !== model && !rig.anim && introDone.current) {
        fitted.current = model;
        startAnim(pf(model, kind, aspect, s.cameraPreset), 1.0);
      }
    }
    warpRef.current = intensity;

    // 인트로 워밍업 — 첫 드로우(컴파일·업로드)를 치른 뒤 프레임 간격이 안정되면 비행을 시작하고 로딩 오버레이를 걷는다.
    // delta 는 직전 프레임이 걸린 시간이라, 무거운 프레임 바로 다음 프레임에서 작은 값이 나오면 그 고비를 넘긴 것이다
    if (rig.intro) {
      rig.intro.frames += 1;
      const settled = rig.intro.frames >= WARM_MIN_FRAMES && delta < WARM_FRAME_S;
      if (s.phase === "warp") {
        // 딥링크(?company=)처럼 모델 도착과 함께 워프가 시작됐다 — 비행은 워프 연출이 맡으므로 인트로는 접고 오버레이만 걷는다
        rig.intro = null;
        markSceneReady();
      } else if (settled || now - rig.intro.startedAt > WARMUP_TIMEOUT_MS) {
        // 워밍업 사이에 모델이 바뀌어 재프레이밍(startAnim)이 이미 카메라를 잡았으면 그 비행을 두고 인트로는 생략한다
        if (!rig.anim) startAnim(rig.intro.pose, INTRO_S);
        rig.intro = null;
        markSceneReady();
      }
    }

    if (rig.anim) {
      const a = rig.anim;
      a.t = Math.min(a.dur, a.t + dt);
      const k = easeOutCubic(a.t / a.dur);
      camera.position.lerpVectors(a.fromPos, a.toPos, k);
      if (controls) {
        controls.target.lerpVectors(a.fromTgt, a.toTgt, k);
        controls.update();
      }
      if (a.t >= a.dur) rig.anim = null;
    }

    cameraBusy.anim = !!rig.anim || s.phase === "warp" || rig.intro !== null;
    if (controls) {
      // 행성을 끄는 동안은 궤도 회전을 멈춘다 — 안 그러면 행성과 카메라가 같은 드래그로 함께 움직인다. 인트로 워밍업 중에도 잠근다
      const free = s.phase !== "warp" && !rig.anim && !dragState.id && !rig.intro;
      if (controls.enabled !== free) controls.enabled = free;
    }
  });
  /* eslint-enable react-hooks/immutability */
  return null;
}

function Effects({ warpRef }: { warpRef: MutableRefObject<number> }) {
  const ca = useRef<{ offset: THREE.Vector2 } | null>(null);
  const offset = useMemo(() => new THREE.Vector2(0.0005, 0.0003), []);
  useFrame(() => {
    if (!ca.current) return;
    const k = warpRef.current;
    ca.current.offset.set(0.0004 + k * 0.003, 0.0003 + k * 0.002);
  });
  return (
    <EffectComposer multisampling={0}>
      <Bloom mipmapBlur intensity={0.35} luminanceThreshold={0.88} luminanceSmoothing={0.2} radius={0.5} />
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <ChromaticAberration ref={ca as any} offset={offset} radialModulation modulationOffset={0.35} />
      <Vignette eskil={false} offset={0.22} darkness={0.82} />
    </EffectComposer>
  );
}

interface ContentProps {
  galaxy: SceneModel | null;
  system: SceneModel | null;
  /** 워프 목적지 배치 — 커밋(0.6s) 시점에 있으면 모프하고, 없으면 기존 팝인으로 폴백한다 */
  next?: SceneModel | null;
  warpRef: MutableRefObject<number>;
  /**
   * PathGalaxyPage(관계 경로 화면) 전용 — 켜면 기본 상태에서 모든 행성에 이름표·로고가 보이고,
   * 호버·클릭으로 무언가에 초점을 맞추면 그 밖의 것들은 옅게 남는 대신 완전히 숨는다.
   * 은하·기업 중심 뷰(전달하지 않는 곳)는 기존 동작 그대로다.
   */
  isolateOnFocus?: boolean;
  /** PathGalaxyPage 전용 — 우측 인텔리전스 패널이 겹칠 자리를 비워 두도록 카메라 시선을 오른쪽으로 민다 (0~1) */
  panelBias?: number;
}

function Content({ galaxy, system, next = null, warpRef, isolateOnFocus = false, panelBias = 0 }: ContentProps) {
  const focusId = useGalaxy((s) => s.focusId);
  const hoveredId = useGalaxy((s) => s.hoveredId);
  const hoveredEdgeId = useGalaxy((s) => s.hoveredEdgeId);
  const selectedEdgeId = useGalaxy((s) => s.selectedEdgeId);
  const minScore = useGalaxy((s) => s.minScore);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const showLabels = useGalaxy((s) => s.showLabels);
  const edgeBudget = useGalaxy((s) => s.edgeBudget);
  const setHovered = useGalaxy((s) => s.setHovered);
  const setHoveredEdge = useGalaxy((s) => s.setHoveredEdge);
  const selectEdge = useGalaxy((s) => s.selectEdge);
  const clearSelection = useGalaxy((s) => s.clearSelection);
  const warpTo = useGalaxy((s) => s.warpTo);
  const selectedId = useGalaxy((s) => s.selectedId);
  const roleFilter = useGalaxy((s) => s.roleFilter);
  const selectCompany = useGalaxy((s) => s.selectCompany);

  const model = focusId ? system : galaxy;
  // 최소 점수·관계 유형 필터 — 경로·역할 계산은 예산(상위만)은 무시하고 이 필터만 지킨다
  const allowed = useMemo(() => (e: SceneEdge) => e.score >= minScore && activeTypes.has(e.type), [minScore, activeTypes]);
  // 경로 기준·게이트는 hoverPath 한 곳에 있다 — HUD 스트립(PathStrip)과 규칙이 어긋나지 않게
  const path = useMemo(() => hoverPath(model, hoveredId, selectedId, allowed)?.path ?? null, [model, hoveredId, selectedId, allowed]);
  // 강조의 주인공 — 호버 중이면 호버 노드, 아니면 미리보기로 고른 기업 (고른 기업은 손을 떼도 이웃·역할이 켜져 있다)
  const focusNode = hoveredId ?? (model && selectedId && model.nodeById.has(selectedId) ? selectedId : null);
  // 주인공의 이웃 역할 — 이웃 링(RoleRings)과 칩 역할 태그(CompanyNodes)가 같은 맵을 쓴다
  const roleTags = useMemo(() => (model && focusNode && model.nodeById.has(focusNode) ? neighborRoles(model, focusNode, allowed) : null), [model, focusNode, allowed]);
  const emphasis = useMemo(
    () =>
      model
        ? computeEmphasis(model, { hoveredId: focusNode, hoveredEdgeId, selectedEdgeId, minScore, activeTypes: activeTypes as Set<string>, budget: edgeBudget, path, roleFilter, isolate: isolateOnFocus })
        : null,
    [model, focusNode, hoveredEdgeId, selectedEdgeId, minScore, activeTypes, edgeBudget, path, roleFilter, isolateOnFocus],
  );
  // Special Link 간선 집합 — 선 위 글자(EdgeLabels)에 ✦ 를 붙인다. 모델당 한 번만 스캔한다
  const surprise = useMemo(() => (model ? surpriseSet(model) : new Set<string>()), [model]);

  // 로고 아틀라스의 부분 업로드(logoAtlas.flush)는 이 씬의 렌더러로 한다 — 떠날 때 풀어 다른 컨텍스트로 복사하지 않게
  const gl = useThree((s) => s.gl);
  useEffect(() => {
    bindLogoAtlasRenderer(gl);
    return () => bindLogoAtlasRenderer(null);
  }, [gl]);

  useEffect(() => {
    document.body.style.cursor = hoveredId || hoveredEdgeId ? "pointer" : "";
    return () => {
      document.body.style.cursor = "";
    };
  }, [hoveredId, hoveredEdgeId]);

  // 별가루 시드는 구성원 목록에서 뽑는다 — 노드 수만 같고 내용이 다른 스냅샷·필터가 같은 별가루를 재생하지 않게.
  // 성단은 산업 하나당 하나라 clusters.length<=1 이 곧 layoutGalaxy 의 single(팔 인력 off) 조건이다
  const dust = useMemo(
    () => (model?.kind === "galaxy" ? { seed: hashString(model.nodes.map((nd) => nd.id).join(",")), spiral: model.clusters.length > 1, radius: model.extent.maxR } : null),
    [model],
  );

  return (
    <>
      <Director warpRef={warpRef} galaxy={galaxy} system={system} next={next} panelBias={panelBias} />
      {/* W A S D 로 옮길 수 있는 궤도 중심의 한계 — 지금 보는 모델의 바깥 반지름에 여유를 둔다 */}
      {/* 팬 경계는 은하 바깥 반지름과 정렬 격자 중 큰 쪽 — 정렬 단계에서 격자 구석의 기업까지 W A S D 로 갈 수 있어야 한다 */}
      <OrbitKeys bound={model ? Math.max(model.extent.maxR * 1.4, model.kind === "galaxy" ? Math.hypot(sortedExtent(model).halfW, sortedExtent(model).halfH) : 0) : undefined} />
      {/* 극각 하한 0.03rad(약 1.7도): 카메라가 정점(y 축)에 정확히 올라서면 lookAt(up=+y)이 무너져 시선이 돈다 — 드래그와 방향키(OrbitKeys)가 같은 한계를 읽는다 */}
      <OrbitControls makeDefault enablePan={false} enableDamping dampingFactor={0.07} rotateSpeed={0.45} zoomSpeed={0.7} minDistance={22} maxDistance={900} minPolarAngle={0.03} maxPolarAngle={Math.PI * 0.62} />
      <fog attach="fog" args={[LAB.bg, 520, 1600]} />
      <Backdrop radius={1700} stars={900} />
      {/* 장난감 우주 조명 — 랩과 동일한 구성 */}
      <ambientLight intensity={0.55} color="#c9d4ff" />
      <hemisphereLight args={["#9ab3ff", "#3a1f5c", 0.6]} />
      <directionalLight position={[-60, 80, 70]} intensity={2.4} color="#fff3e0" />
      {/* 행성을 끌어 옮긴 직후의 클릭은 "빈 곳 클릭"이 아니다 */}
      <group onPointerMissed={(e) => e.button === 0 && !dragState.blockClick && clearSelection()}>
        {model && emphasis && (
          <group key={model.kind + (model.centerId ?? "")}>
            {/* 별가루는 은하 뷰에만, 격자보다 먼저 그려 가장 뒤에 깔린다 */}
            {dust && <GalaxyDust seed={dust.seed} radius={dust.radius} spiral={dust.spiral} warpRef={warpRef} />}
            {/* 주가 고도는 은하 뷰에만 — 기업 중심 뷰의 궤도는 depth 가 정하므로 높이에 다른 의미를 얹지 않는다 */}
            {model.kind === "galaxy" && <AltitudeDriver model={model} />}
            {/* 펼치기(도크 슬라이더) — 은하 → 평면 → 크기순 격자. 고도와 같은 live 버퍼를 쓰되, 펼치는 동안은 고도 구동기가 물러난다 */}
            {model.kind === "galaxy" && <SpreadDriver model={model} />}
            {/* 첫 방문 투어의 스포트라이트 앵커 — 대상이 없으면 아무 일도 하지 않는다 */}
            {model.kind === "galaxy" && <TourAnchor model={model} />}
            <RelationLines
              model={model}
              edges={model.edges}
              emphasis={emphasis}
              onHover={setHoveredEdge}
              onSelect={selectEdge}
              intensity={model.kind === "galaxy" ? 0.85 : 1}
              hoveredEdgeId={hoveredEdgeId}
              warpFx
            />
            <EdgeArrows model={model} emphasis={emphasis} />
            {/* 선 중간의 관계 종류 글자(P0-5) · 허브 링 / 0선 기준면 / depth 링(P0-6) */}
            <EdgeLabels model={model} emphasis={emphasis} surprise={surprise} allEdges={isolateOnFocus} />
            <ReferenceRings model={model} />
            <RoleRings model={model} hoveredId={focusNode} roles={roleTags} />
            {/* 클릭 = 미리보기(뉴스·연결 단계 패널, 이웃 강조) · 더블클릭 = 그 기업의 관계망으로 워프 */}
            <CompanyNodes model={model} emphasis={emphasis} hoveredId={focusNode} onHover={setHovered} onClick={selectCompany} onDoubleClick={warpTo} showLabels={showLabels} roleTags={roleTags} />
          </group>
        )}
      </group>
      {/* 은하 전용 행성의 이탈 — keyed group 밖이라 모델이 교체돼도 계획이 끝날 때까지 살아 있다 */}
      <GhostNodes />
      <Warp intensityRef={warpRef} />
      <Effects warpRef={warpRef} />
      <AdaptiveDpr />
    </>
  );
}

export default function Scene(props: Omit<ContentProps, "warpRef">) {
  const warpRef = useRef(0);
  // 씬이 사라지면 R3F 가 WebGL 컨텍스트를 버린다(forceContextLoss) — 다음 마운트는 셰이더를 다시 컴파일하므로 준비 신호도 되돌려 오버레이가 다시 덮게 한다
  useEffect(() => () => resetSceneReady(), []);
  return (
    <Canvas
      dpr={[1, 1.75]}
      gl={{ antialias: false, powerPreference: "high-performance", alpha: false }}
      camera={{ fov: 52, near: 0.5, far: 3000, position: FALLBACK_POSE.galaxy.pos }}
      raycaster={{ params: { Line: { threshold: 1.0 }, Points: { threshold: 1 }, Mesh: {}, LOD: {}, Sprite: {} } }}
      onCreated={(state) => {
        // 플랫 컬러가 그대로 나오도록 톤매핑은 끈다 (랩과 동일)
        state.gl.toneMapping = THREE.NoToneMapping;
        state.gl.setClearColor(LAB.bg, 1);
      }}
    >
      <Content {...props} warpRef={warpRef} />
    </Canvas>
  );
}
