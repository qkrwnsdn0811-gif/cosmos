import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneModel, SceneNode } from "@/lib/graph";
import { ROLE_META, ROLE_ORDER, roleColor, type RoleKey } from "@/lib/roles";
import { useNarrowViewport } from "@/lib/viewport";
import { HOLD_MS, useGalaxy } from "@/store/galaxy";
import { altitudeState } from "./altitude";
import { touch } from "./bufferUtil";
import { cameraBusy } from "./cameraState";
import { CHIP_PILL, chipAspect, chipFragment, chipHit, chipVertex, roleWidth } from "./chipShader";
import type { Emphasis } from "./emphasis";
import { ATLAS_CELL_UV, logoAtlasTexture, nodeLogoCell } from "./logoAtlas";
import { discFragment, discHit, discVertex } from "./logoDiscShader";
import { dragState, liveFor, morphState, type LiveTransform } from "./morph";
import { ensureNameCell, nameAtlasTexture, type NameCell } from "./nameAtlas";
import { animFor } from "./nodeAnim";
import { spreadState } from "./spread";
import { dotTexture, ringTexture } from "./textures";
import { PLANET_RADIUS_K } from "./toon";

interface Props {
  model: SceneModel;
  emphasis: Emphasis;
  /** 지금 주인공인 기업 — 호버한 기업, 없으면 선택한 기업 (밝게·크게, 이웃 역할 태그의 기준) */
  hoveredId: string | null;
  /** 선택한 기업 — 다른 행성에 마우스를 올려 hoveredId 가 바뀌어도 이름표를 계속 띄운다 */
  selectedId?: string | null;
  onHover: (id: string | null) => void;
  onClick: (id: string) => void;
  /** 이름표에 기업명을 띄울 수 있게 할지. 켜 두어도 이름은 호버·선택·중심 기업에서만 나온다 (끄면 이름은 아예 없고 역할 태그만 남는다) */
  showLabels?: boolean;
  /** 강조 고정(pinned) 기업에도 이름을 띄운다 — 관계 경로 화면처럼 기본 상태에서 모든 이름을 보여 주는 곳 */
  pinnedNames?: boolean;
  /** 더블클릭(두 번째 click, nativeEvent.detail ≥ 2) — 그 기업의 관계망으로. 첫 클릭은 onClick(미리보기)으로 이미 처리돼 있다 */
  onDoubleClick?: (id: string) => void;
  /** 호버 노드의 이웃 → 역할. 그 이웃 행성 위에 유형색 역할 태그(공급사·고객…)가 뜬다 */
  roleTags?: Map<string, RoleKey> | null;
}

const tmpObj = new THREE.Object3D();
const TMP = new THREE.Vector3();
const TO_CAM = new THREE.Vector3();
const UPV = new THREE.Vector3();
const NDC = new THREE.Vector3();
/** 드래그 스크래치 — 한 번에 하나만 끌 수 있으므로 모듈에 둔다 */
const DRAG_PLANE = new THREE.Plane();
const DRAG_HIT = new THREE.Vector3();
const DRAG_OFF = new THREE.Vector3();
const DRAG_POS = new THREE.Vector3();
const DRAG_DIR = new THREE.Vector3();
const PTR = new THREE.Vector2();
const RAY = new THREE.Raycaster();
/** 이만큼(px) 끌어야 드래그로 친다 — 그 아래 흔들림은 그대로 클릭(워프·핀) */
const DRAG_SLOP = 6;
/**
 * 드래그 진행 상태 — 포인터는 하나뿐이라 인스턴스별로 나눌 필요가 없고, useRef(...).current 를 훅 의존성으로 넘기면
 * react-hooks/refs 에 걸리므로 CHIP_ORDER 처럼 모듈에 둔다.
 * id 는 포인터를 누른 후보 노드(아직 드래그는 아니다), active 는 슬롭을 넘겨 실제로 끄는 중이다.
 * live 는 그 노드가 속한 버퍼 — 씬이 둘(은하·미니) 겹쳐 있어도 자기 것이 아닌 드래그에는 반응하지 않게 하는 주인 표식이다.
 */
/** touchLike: 이 입력이 터치이거나 좁은 화면(모바일 배치)에서 왔는지 — 뒤따르는 click 의 "같은 행성 재탭 = 더블클릭" 판정이 본다 */
const DRAG = { id: null as string | null, index: -1, x: 0, y: 0, active: false, cursor: "", live: null as LiveTransform | null, touchLike: false };
/**
 * 드래그 관성 — 행성은 커서를 그대로 따라가지 않는다. 커서 이동의 일부(DRAG_SENS)만 목표점으로 삼고,
 * 스프링(약한 언더댐핑)으로 목표를 향해 미끄러지며, 손을 뗀 뒤에도 남은 관성으로 안착한다.
 * 목적은 정밀 배치가 아니라 "만지면 반응한다"는 인터랙티브한 감각이다.
 */
const DRAG_SENS = 0.6;
const GLIDE_K = 22; // 스프링 강성 — 클수록 빨리 따라온다
const GLIDE_ZETA = 0.72; // 감쇠비 — 1 미만이라 살짝 넘치고 되돌아온다
const GLIDE = { live: null as LiveTransform | null, index: -1, active: false, target: new THREE.Vector3(), vel: new THREE.Vector3(), origin: new THREE.Vector3() };
/** built 별 "헤일로 위치를 마지막으로 올린 드래그 버전" */
const HALO_SYNC = new WeakMap<object, { version: number }>();

/**
 * 드래그 후보를 잡아 둔다 (실제로 옮기기 시작하는 건 슬롭을 넘긴 pointermove).
 * dragState.id 는 여기서 바로 세운다 — 궤도 컨트롤은 프레임마다 이 값을 보고 꺼지므로, 슬롭을 넘긴 뒤에 세우면
 * 그 사이에 들어온 pointermove 한 번이 카메라를 돌려 버린다. 행성을 누른 동안 카메라가 멈추는 건 의도한 동작이다.
 * (렌더 중에 만들어지는 이벤트 핸들러가 모듈 변수를 직접 고치면 react-hooks/immutability 에 걸리므로 touch() 처럼 밖으로 뺐다)
 */
function armDrag(id: string, index: number, x: number, y: number, live: LiveTransform, touchLike: boolean) {
  DRAG.id = id;
  DRAG.touchLike = touchLike;
  DRAG.index = index;
  DRAG.x = x;
  DRAG.y = y;
  DRAG.active = false;
  DRAG.live = live;
  dragState.id = id;
  // 앞선 드래그의 클릭 차단이 남아 있을 일은 없지만(rAF 에서 풀린다), 새 입력이 시작되면 확실히 턴다
  dragState.blockClick = false;
}
/**
 * 길게 누르기(모바일에서 관계망으로) — 후보 기업과 타이머. 손을 떼거나 슬롭을 넘겨 끌기 시작하면 취소하고, 다 채우면 워프한다.
 * 터치·좁은 화면에서만 무장한다(마우스 데스크톱은 더블클릭). fired 는 워프 직후 손을 뗄 때 따라오는 click 한 번을 삼키기 위한 표시다
 */
const HOLD: { id: string | null; timer: ReturnType<typeof setTimeout> | null; fired: boolean; live: LiveTransform | null } = { id: null, timer: null, fired: false, live: null };
/** 같은 행성을 이 시간 안에 두 번 탭하면 더블클릭으로 본다 — 터치는 click.detail 이 늘지 않고 dblclick 도 오지 않는 브라우저가 많다 */
const TAP_DOUBLE_MS = 350;
const LAST_TAP: { id: string | null; t: number } = { id: null, t: 0 };
function clearHold() {
  if (HOLD.timer !== null) clearTimeout(HOLD.timer);
  HOLD.timer = null;
  if (HOLD.id !== null) {
    HOLD.id = null;
    HOLD.live = null;
    useGalaxy.getState().setHold(null);
  }
}
/**
 * 길게 누르기 타이머를 건다 — HOLD_MS 동안 취소되지 않으면 끌기 후보를 버리고 fire(워프)를 부른다.
 * (렌더 중에 만들어지는 핸들러가 모듈 변수를 고치면 react-hooks/immutability 에 걸리므로 armDrag 처럼 밖으로 뺐다)
 */
function armHoldTimer(id: string, x: number, y: number, live: LiveTransform, fire: () => void) {
  clearHold();
  HOLD.id = id;
  HOLD.fired = false;
  HOLD.live = live;
  useGalaxy.getState().setHold({ id, x, y });
  HOLD.timer = setTimeout(() => {
    HOLD.timer = null;
    if (HOLD.id !== id) return;
    HOLD.id = null;
    HOLD.live = null;
    HOLD.fired = true;
    useGalaxy.getState().setHold(null);
    // 잡아 둔 끌기 후보는 버린다 — 워프하는 동안 손가락이 움직여도 행성이 끌리지 않게
    DRAG.id = null;
    DRAG.live = null;
    dragState.id = null;
    navigator.vibrate?.(12);
    fire();
  }, HOLD_MS);
}
/** 같은 행성을 TAP_DOUBLE_MS 안에 다시 탭했는지 — 판정과 함께 마지막 탭을 기록한다(더블로 판정되면 기록을 비워 세 번째 탭이 또 더블이 되지 않게) */
function tapIsDouble(id: string) {
  // 마우스 데스크톱은 브라우저의 detail 만 믿는다 — OS 더블클릭 간격이 350ms 보다 짧게 설정된 사용자가 따로 두 번 누른 것을 워프로 잡지 않게
  if (!DRAG.touchLike) {
    LAST_TAP.id = null;
    return false;
  }
  const now = performance.now();
  const dbl = LAST_TAP.id === id && now - LAST_TAP.t < TAP_DOUBLE_MS;
  LAST_TAP.id = dbl ? null : id;
  LAST_TAP.t = now;
  return dbl;
}
/** 칩 겹침 판정용 후보 순서·채택 목록 — 프레임마다 비우고 채우는 스크래치라 모듈에 둔다 */
const CHIP_ORDER: number[] = [];
const CHIP_KEPT: number[] = [];
/**
 * 칩 겹침 걸러내기 — 우선순위(prio, 동률은 큰 행성이 되도록 크기를 소수부로 섞어 둔다) 순으로 훑으며,
 * 이미 채택된 칩 사각형(rect: left, top, right, bottom)과 CHIP_GAP 안으로 겹치는 후보는 목표 알파를 0 으로 만든다.
 * 후보(order)는 티어 LOD 를 통과한 수십 개뿐이라 제곱 비교로도 충분하다
 */
function cullOverlappingChips(order: number[], rect: Float32Array, prio: Float32Array, target: Float32Array) {
  order.sort((x, y) => prio[y] - prio[x]);
  CHIP_KEPT.length = 0;
  for (const i of order) {
    const o = i * 4;
    const l = rect[o] - CHIP_GAP;
    const tp = rect[o + 1] - CHIP_GAP;
    const rr = rect[o + 2] + CHIP_GAP;
    const b = rect[o + 3] + CHIP_GAP;
    let hit = false;
    for (const j of CHIP_KEPT) {
      const q = j * 4;
      if (l < rect[q + 2] && rect[q] < rr && tp < rect[q + 3] && rect[q + 1] < b) {
        hit = true;
        break;
      }
    }
    if (hit) target[i] = 0;
    else CHIP_KEPT.push(i);
  }
}
/**
 * 칩 겹침 판정의 여백(px) — 화면에 실제로 그려질 칩 사각형이 이 여백 안으로 겹치면 우선순위 낮은 쪽을 숨긴다.
 * 예전 방식(행성 위치를 96×28 격자에 넣어 같은 칸만 겹침으로 침)은 칩이 칸보다 넓어지자 무력해졌다 — 26px 칩은 폭이 100~150px 라
 * 옆 칸의 칩과 그대로 겹쳤다(기본 자세에서 겹치는 쌍 실측 4.3/프레임). 사각형끼리 재면 폭이 얼마든 정확하다
 */
const CHIP_GAP = 4;
/**
 * 이름표 목표 화면 높이(px)와 월드 높이 클램프 — 코앞에서도 너무 작아지지 않고 멀리서도 행성을 통째로 덮지 않게.
 * 로고 원이 칩 왼쪽에 붙어 있던 시절에는 40px 칩에 글자 ≈ 22px 였다. 행성이 로고가 되면서 이름표에는 글자만 남아
 * 캡슐을 32px 로 얇게 하고 글자 영역(chipShader CHIP_NAME_H)을 0.8 로 넓혀 글자 크기(≈ 21px)는 거의 그대로 뒀다.
 * 상한 10 은 은하 기본 자세(카메라 ≈ 265~310)에서 캔버스 세로 790px 기준 이름표가 30px 남짓으로 줄어드는 값이다
 */
const CHIP_PX = 32;
const CHIP_MIN = 1.3;
const CHIP_MAX = 10.0;
/** 이름표 아래 끝과 행성 테두리 사이 — 이름표 높이의 비율 */
const CHIP_LIFT = 0.2;
/**
 * 행성 원판을 행성 중심에서 카메라 쪽으로 반지름 × 이 배만큼 내놓는다. 예전 구체의 앞면 자리라서, 행성 뒤로 지나가는 관계선은
 * 원판 뒤에 가려지고(깊이), 행성 중심에 있는 헤일로 점광은 원판 둘레로만 비친다. 시선 위로 옮기므로 화면 위치는 그대로다
 */
const DISC_LIFT = 1.0;
/** 피킹에서 무시하는 원판·칩 알파 — 이 아래는 보이지 않는데 포인터를 가로채면 안 된다 */
const PICK_ALPHA = 0.05;

/**
 * InstancedMesh 레이캐스트를 감싸 accept 를 통과한 히트만 남긴다.
 * R3F 는 핸들러가 stopPropagation 을 부르지 않아도 히트 인스턴스를 hovered 맵에 등록하고(이후 onPointerOut 발생),
 * 한 번 stop 된 인스턴스 위에서는 핸들러 전에 전파를 다시 막는다. 그래서 투명한 스템·모서리를 핸들러에서 걸러내는 것으로는
 * 부족하고, 애초에 레이캐스트 결과에서 빼야 뒤의 행성·빔·다른 칩이 정상적으로 호버된다.
 */
function filteredRaycast(accept: (instanceId: number, uv: THREE.Vector2) => boolean) {
  const tmp: THREE.Intersection[] = [];
  return function raycast(this: THREE.InstancedMesh, rc: THREE.Raycaster, out: THREE.Intersection[]) {
    tmp.length = 0;
    THREE.InstancedMesh.prototype.raycast.call(this, rc, tmp);
    for (const h of tmp) if (h.instanceId !== undefined && h.uv && accept(h.instanceId, h.uv)) out.push(h);
  };
}

/**
 * 기업 노드 = 원형으로 자른 기업 로고 하나 (로고 행성, InstancedMesh 1회).
 * - 로고는 로고 아틀라스(logoAtlas — 128px 셀, 로고가 없으면 이니셜 모노그램)에서 읽고, 가장자리에 얇은 산업색 테두리를 둘러
 *   성단(산업)이 로고 사이에서도 읽히게 한다. 원판은 언제나 카메라를 향한다(빌보드)
 * - 크기는 연결 가중치 + 시가총액(node.size, graph.ts nodeSize) — 예전 툰 구체와 같은 반지름이라 배치·관계선·역할 링이 그대로 맞는다.
 *   호버·중심·강조로 크기와 밝기를 프레임마다 lerp 하고, 흐려지는 행성은 채도도 뺀다. 위치는 live 버퍼(morph.ts)에서 읽는다
 * - 기업명은 평소에 띄우지 않는다. 호버·선택한 기업(과 기업 중심 뷰의 중심)에만 행성 바로 위에 이름표 캡슐(InstancedMesh 1회)을 띄우고,
 *   호버 기업의 이웃에는 같은 자리에 역할 태그만 띄운다. 이름표는 화면상 약 32px 높이이고 칩 사각형 겹침 판정(cullOverlappingChips)으로 겹침을 막는다
 * - 멀리서도 위치가 보이도록 아주 약한 헤일로 점광을 함께 둔다
 * - 행성·이름표를 끌면 그 행성을 옮길 수 있다. 옮긴 자리는 live 버퍼에만 남으므로 새로고침하면 레이아웃 좌표로 돌아간다
 */
export default function CompanyNodes({ model, emphasis, hoveredId, selectedId = null, onHover, onClick, showLabels = true, pinnedNames = false, onDoubleClick, roleTags = null }: Props) {
  // 좁은 화면(모바일 배치)에서는 마우스로도 길게 누르기를 받는다 — 터치 여부만 보면 데스크톱 브라우저의 모바일 에뮬레이션에서 확인할 수 없다
  const narrow = useNarrowViewport();
  const discs = useRef<THREE.InstancedMesh>(null);
  const chips = useRef<THREE.InstancedMesh>(null);
  const ring = useRef<THREE.Sprite>(null);
  const n = model.nodes.length;
  const live = liveFor(model);
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);

  // 모프 없는 모델 교체(필터·폴백)는 기존처럼 팝인하고, 워프 모프 중 마운트는 이어받은 상태를 보존한다
  useEffect(() => {
    if (morphState.plan) return;
    model.nodes.forEach((node) => {
      animFor(node.id).scale = 0.001;
    });
  }, [model]);

  // 원판·이름표·헤일로 버퍼
  const built = useMemo(() => {
    const color = new Float32Array(n * 3);
    /** 레이아웃 반지름(월드) — 예전 구체와 같은 값이라 관계선·화살촉·역할 링(PLANET_RADIUS_K 를 쓰는 곳)과 맞는다 */
    const radius = new Float32Array(n);
    const bright = new Float32Array(n).fill(1);
    const emph = new Float32Array(n).fill(1);
    const alpha = new Float32Array(n);
    const cell = new Float32Array(n * 2);
    const size = new Float32Array(n);
    const haloA = new Float32Array(n).fill(1);
    const nameCells: (NameCell | null)[] = [];
    const name = new Float32Array(n * 4);
    const c = new THREE.Color();
    const center = new THREE.Vector3();
    // 역할 태그 라벨을 호버 시점에 처음 구우면 그 프레임에 2048² 이름 아틀라스가 통째로 다시 업로드되고 밉맵도 재생성된다.
    // 어차피 한 번 일어나는 이 루프의 업로드에 합쳐 미리 구워 두면, 호버 때는 캐시 히트만 남는다
    ROLE_ORDER.forEach((r) => ensureNameCell(ROLE_META[r].label));
    model.nodes.forEach((node, i) => {
      c.set(node.color);
      color[i * 3] = c.r;
      color[i * 3 + 1] = c.g;
      color[i * 3 + 2] = c.b;
      radius[i] = node.size * PLANET_RADIUS_K;
      size[i] = node.size;
      center.x += node.pos[0];
      center.y += node.pos[1];
      center.z += node.pos[2];
      const [u, v] = nodeLogoCell(node);
      cell[i * 2] = u;
      cell[i * 2 + 1] = v;
      const nc = ensureNameCell(node.label ?? node.name);
      nameCells.push(nc);
      if (nc) {
        name[i * 4] = nc.u;
        name[i * 4 + 1] = nc.v;
        name[i * 4 + 2] = nc.w;
        name[i * 4 + 3] = nc.h;
      }
    });
    // 레이캐스트용 경계구 — InstancedMesh 는 첫 레이캐스트 때의 행렬로 한 번만 계산하므로 여유를 두고 직접 준다
    if (n > 0) center.multiplyScalar(1 / n);
    let reach = 0;
    model.nodes.forEach((node) => {
      reach = Math.max(reach, TMP.fromArray(node.pos).distanceTo(center) + node.size * 4);
    });
    const bounds = new THREE.Sphere(center, reach + 40);

    const dyn = (arr: Float32Array, itemSize: number) => {
      const a = new THREE.InstancedBufferAttribute(arr, itemSize);
      a.setUsage(THREE.DynamicDrawUsage);
      return a;
    };

    // 원판: 2×2 평면이라 인스턴스 스케일이 곧 반지름이다
    const geo = new THREE.PlaneGeometry(2, 2);
    geo.setAttribute("aColor", new THREE.InstancedBufferAttribute(color, 3));
    geo.setAttribute("aCell", new THREE.InstancedBufferAttribute(cell, 2));
    const brightAttr = dyn(bright, 1);
    const emphAttr = dyn(emph, 1);
    const alphaAttr = dyn(alpha, 1);
    geo.setAttribute("aBright", brightAttr);
    geo.setAttribute("aEmph", emphAttr);
    geo.setAttribute("aAlpha", alphaAttr);

    // 이름표: 로컬 x ∈ [-0.5, 0.5], y ∈ [0, 1] — 아래 가운데가 원점이라 행성 바로 위 가운데에 놓인다. x 폭은 인스턴스 행렬로 aspect 배 늘린다
    const chipGeo = new THREE.PlaneGeometry(1, 1);
    chipGeo.translate(0, 0.5, 0);
    chipGeo.setAttribute("aColor", new THREE.InstancedBufferAttribute(color, 3));
    chipGeo.setAttribute("aName", new THREE.InstancedBufferAttribute(name, 4));
    const aspectArr = new Float32Array(n);
    const chipAspectAttr = dyn(aspectArr, 1);
    // (alpha, emph, bright, nameOn) 을 vec4 하나로 — 정점 속성 수를 16 개 한도 아래로 유지한다 (chipShader 주석 참고)
    const chipStateArr = new Float32Array(n * 4);
    for (let i = 0; i < n; i += 1) {
      chipStateArr[i * 4 + 1] = 1;
      chipStateArr[i * 4 + 2] = 1;
    }
    const chipState = dyn(chipStateArr, 4);
    const chipRole = dyn(new Float32Array(n * 4), 4);
    const chipRoleColor = dyn(new Float32Array(n * 3), 3);
    chipGeo.setAttribute("aAspect", chipAspectAttr);
    chipGeo.setAttribute("aState", chipState);
    chipGeo.setAttribute("aRole", chipRole);
    chipGeo.setAttribute("aRoleColor", chipRoleColor);

    // 헤일로는 live 버퍼를 그대로 정점 위치로 쓴다 (모프 중에는 프레임마다 업로드)
    const haloGeo = new THREE.BufferGeometry();
    const haloPos = new THREE.BufferAttribute(liveFor(model).pos, 3);
    haloPos.setUsage(THREE.DynamicDrawUsage);
    haloGeo.setAttribute("position", haloPos);
    haloGeo.setAttribute("color", new THREE.BufferAttribute(color.slice(), 3));
    const haloSize = new THREE.BufferAttribute(size, 1);
    haloSize.setUsage(THREE.DynamicDrawUsage);
    const haloAlpha = new THREE.BufferAttribute(haloA, 1);
    haloAlpha.setUsage(THREE.DynamicDrawUsage);
    haloGeo.setAttribute("aSize", haloSize);
    haloGeo.setAttribute("aAlpha", haloAlpha);
    return { radius, geo, brightAttr, emphAttr, alphaAttr, chipGeo, chipAspect: chipAspectAttr, chipState, chipRole, chipRoleColor, aspectArr, nameCells, bounds, haloGeo, haloPos, haloSize, haloAlpha };
  }, [model, n]);
  useEffect(
    () => () => {
      built.geo.dispose();
      built.chipGeo.dispose();
      built.haloGeo.dispose();
    },
    [built],
  );

  // 역할 태그 — 호버가 바뀔 때만 이웃 이름표의 aRole(이름 아틀라스의 역할 라벨 셀)·aRoleColor 를 쓴다. 폭은 프레임 루프가 aRole 에서 읽는다
  useEffect(() => {
    const roleArr = built.chipRole.array as Float32Array;
    const colArr = built.chipRoleColor.array as Float32Array;
    roleArr.fill(0);
    if (roleTags) {
      const c = new THREE.Color();
      model.nodes.forEach((node, i) => {
        const role = roleTags.get(node.id);
        if (!role) return;
        const cell = ensureNameCell(ROLE_META[role].label);
        if (!cell) return;
        roleArr[i * 4] = cell.u;
        roleArr[i * 4 + 1] = cell.v;
        roleArr[i * 4 + 2] = cell.w;
        roleArr[i * 4 + 3] = cell.h;
        c.set(roleColor(role));
        colArr[i * 3] = c.r;
        colArr[i * 3 + 1] = c.g;
        colArr[i * 3 + 2] = c.b;
      });
    }
    touch(built.chipRole, built.chipRoleColor);
  }, [roleTags, model, built]);

  // 프레임 스크래치 — 이름표 높이·목표 알파·화면 사각형(left, top, right, bottom)·우선순위 (겹침 판정이 끝난 뒤 두 번째 루프에서 쓴다)
  const scratch = useMemo(() => ({ h: new Float32Array(n), target: new Float32Array(n), rect: new Float32Array(n * 4), prio: new Float32Array(n) }), [n]);

  useEffect(() => {
    const init = (m: THREE.InstancedMesh | null, ud: Record<string, unknown>) => {
      if (!m) return;
      Object.assign(m.userData, ud);
      m.boundingSphere = built.bounds.clone();
      // 행렬을 프레임마다 덮어쓰므로 usage 힌트도 동적으로 (기본은 STATIC_DRAW)
      m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      for (let i = 0; i < n; i += 1) {
        tmpObj.position.set(0, 0, 0);
        tmpObj.quaternion.identity();
        tmpObj.scale.setScalar(0.001);
        tmpObj.updateMatrix();
        m.setMatrixAt(i, tmpObj.matrix);
      }
      m.instanceMatrix.needsUpdate = true;
    };
    // 두 메시 모두 raycast 를 감싸 원판·캡슐 안의 보이는 히트만 남기므로, 간선 피킹(RelationLines.nodeAhead)은 pick 만 보면 된다
    init(discs.current, { pick: "node" });
    init(chips.current, { pick: "node" });
  }, [model, n, built]);

  const discUniforms = useMemo(() => ({ uAtlas: { value: logoAtlasTexture() }, uCell: { value: ATLAS_CELL_UV } }), []);
  const chipUniforms = useMemo(() => ({ uNames: { value: nameAtlasTexture() }, uPill: { value: new THREE.Color(CHIP_PILL) } }), []);
  const haloUniforms = useMemo(() => ({ uMap: { value: dotTexture() } }), []);

  /* eslint-disable react-hooks/immutability -- R3F updates live positions and GPU buffers imperatively in its frame callback. */
  useFrame((state, dt) => {
    // 드래그 관성 — 목표점을 향한 스프링. 손을 뗀 뒤에도 속도가 남아 있는 동안 계속 미끄러진다
    if (GLIDE.active && GLIDE.live === live) {
      const h = Math.min(dt, 0.05);
      const o = GLIDE.index * 3;
      const px = live.pos[o];
      const py = live.pos[o + 1];
      const pz = live.pos[o + 2];
      const c = 2 * GLIDE_ZETA * Math.sqrt(GLIDE_K);
      const v = GLIDE.vel;
      v.x += ((GLIDE.target.x - px) * GLIDE_K - v.x * c) * h;
      v.y += ((GLIDE.target.y - py) * GLIDE_K - v.y * c) * h;
      v.z += ((GLIDE.target.z - pz) * GLIDE_K - v.z * c) * h;
      live.pos[o] = px + v.x * h;
      live.pos[o + 1] = py + v.y * h;
      live.pos[o + 2] = pz + v.z * h;
      dragState.version += 1;
      // 손을 뗀 뒤 목표에 충분히 가까워지고 느려지면 스냅해서 멈춘다
      if (!DRAG.active && GLIDE.target.distanceToSquared(TMP.set(live.pos[o], live.pos[o + 1], live.pos[o + 2])) < 1e-4 && v.lengthSq() < 1e-4) {
        live.pos[o] = GLIDE.target.x;
        live.pos[o + 1] = GLIDE.target.y;
        live.pos[o + 2] = GLIDE.target.z;
        GLIDE.active = false;
        GLIDE.live = null;
      }
    }
    const k = 1 - Math.pow(0.001, dt);
    // 원판 알파·이름표 페이드는 더 빠르게 (0.3s 안에 거의 끝난다)
    const kFast = 1 - Math.pow(0.0001, dt);
    const t = state.clock.elapsedTime;
    const cam = state.camera;
    const discMesh = discs.current;
    const chipMesh = chips.current;
    const morphing = morphState.plan !== null;

    // 이름표 배치 기준: 카메라 상 방향, 화면 CHIP_PX 에 해당하는 월드 높이 계수
    UPV.set(0, 1, 0).applyQuaternion(cam.quaternion);
    const persp = cam as THREE.PerspectiveCamera;
    const fov = persp.isPerspectiveCamera ? persp.fov : 52;
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    const pxK = (2 * tanHalf * CHIP_PX) / Math.max(1, state.size.height);
    const W = state.size.width;
    const H = state.size.height;
    // 겹침 우선순위: 호버 > 선택 > 중심 기업 > 강조 고정 > 나머지. 호버에 별도 등급이 없으면 겹친 큰 이웃의 역할 태그가
    // 호버한 기업의 이름표를 밀어내 숨긴다
    const prioOf = (nd: SceneNode) => (nd.id === hoveredId ? 5 : nd.id === selectedId ? 4 : nd.id === model.centerId ? 3 : emphasis.pinned.has(nd.id) ? 2 : 1);
    CHIP_ORDER.length = 0;
    const roleArr = built.chipRole.array as Float32Array;
    const brightArr = built.brightAttr.array as Float32Array;
    const emphArr = built.emphAttr.array as Float32Array;
    const alphaArr = built.alphaAttr.array as Float32Array;

    for (let i = 0; i < n; i += 1) {
      const node = model.nodes[i];
      const a = animFor(node.id);
      const emph = emphasis.node.get(node.id) ?? 1;
      const isHover = node.id === hoveredId;
      const isCenter = node.id === model.centerId;
      const pinned = emphasis.pinned.has(node.id);
      // 보이는 선이 없는 행성은 작아지며 사라진다 (emphasis.hidden — 은하는 관계가 있는 기업만 띄운다)
      const hidden = emphasis.hidden.has(node.id);
      const targetScale = hidden ? 0.001 : (isHover ? 1.18 : 1) * (isCenter ? 1.1 : 1) * (0.7 + 0.3 * emph);
      const targetBright = (isHover ? 1.15 : isCenter ? 1.06 : 1.0) * (0.42 + 0.58 * emph);
      const s = (a.scale += (targetScale - a.scale) * k);
      const br = (a.bright += (targetBright - a.bright) * k);
      // 원판 알파 — 숨는 행성은 줄어드는 동안 함께 사라지고, 다 사라지면 피킹에서도 빠진다
      const da = (a.tagA += ((hidden ? 0 : 1) - a.tagA) * kFast);

      TMP.fromArray(live.pos, i * 3);
      TO_CAM.copy(cam.position).sub(TMP);
      const dist = TO_CAM.length();
      TO_CAM.multiplyScalar(1 / Math.max(1e-6, dist));
      const r = built.radius[i] * s * live.scale[i];

      // 로고 원판 — 행성 중심에서 카메라 쪽으로 DISC_LIFT·r 나온 자리(예전 구체의 앞면)에 빌보드로 놓는다
      if (discMesh) {
        if (da < 0.01) {
          tmpObj.position.set(0, 0, 0);
          tmpObj.scale.setScalar(0.001);
        } else {
          tmpObj.position.copy(TMP).addScaledVector(TO_CAM, r * DISC_LIFT);
          tmpObj.scale.setScalar(Math.max(1e-3, r));
        }
        tmpObj.quaternion.copy(cam.quaternion);
        tmpObj.updateMatrix();
        discMesh.setMatrixAt(i, tmpObj.matrix);
        brightArr[i] = br;
        emphArr[i] = emph;
        alphaArr[i] = da;
      }
      (built.haloSize.array as Float32Array)[i] = r * 3.2 * (0.5 + 0.5 * emph);
      (built.haloAlpha.array as Float32Array)[i] = hidden ? 0 : (0.15 + 0.85 * emph) * 0.22;

      // 이름표 내용 — 기업명은 호버·선택·중심 기업에서만(pinnedNames 면 강조 고정 기업까지), 역할 태그는 호버 기업의 이웃에서만.
      // 둘 다 없으면 폭이 0 이라 띄우지 않는다. 폭은 겹침 판정(아래)과 배치(두 번째 루프)가 같은 값을 봐야 해서 이 프레임 값으로 여기서 확정한다 —
      // 호버가 옮겨 가는 순간 폭이 바로 뛰므로 직전 프레임 값을 쓰면 그 프레임에 이웃 이름표와 겹친다.
      // 이름을 켜고 끄는 건 즉시 하고, 나타나는 감각은 이름표 알파 페이드(chipA)가 맡는다
      a.nameOn = showLabels && (isHover || node.id === selectedId || isCenter || (pinnedNames && pinned)) ? 1 : 0;
      const aspect = (built.aspectArr[i] = chipAspect(built.nameCells[i]?.aspect ?? 0, roleWidth(roleArr[i * 4 + 2], roleArr[i * 4 + 3]), a.nameOn));

      // 이름표 목표 알파: 내용이 있으면 강조만큼. 모프 중에는 중심만
      scratch.h[i] = THREE.MathUtils.clamp(dist * pxK, CHIP_MIN, CHIP_MAX);
      let ct = aspect > 0 ? 0.15 + 0.85 * emph : 0;
      if ((morphing && !isCenter) || hidden) ct = 0;
      if (ct > 0.01) {
        // 카메라 뒤는 제외. 앞이면 이름표가 그려질 화면 사각형을 재어 두고, 루프가 끝난 뒤 우선순위 순으로 겹침을 걸러낸다
        // 두 번째 루프의 배치처럼 원판 자리(카메라 쪽으로 DISC_LIFT·r)를 투영한다 (화면 가장자리에서는 이 오프셋이 몇 px 옆으로 새기 때문)
        NDC.copy(TMP).addScaledVector(TO_CAM, r * DISC_LIFT).applyMatrix4(cam.matrixWorldInverse);
        if (NDC.z >= -1e-3) ct = 0;
        else {
          // 이 깊이에서 월드 1 이 차지하는 픽셀 — 빌보드라 화면 크기는 여기서 그대로 정해진다
          const pxw = H / (2 * -NDC.z * tanHalf);
          NDC.applyMatrix4(cam.projectionMatrix);
          const px = (NDC.x + 1) * 0.5 * W;
          const py = (1 - NDC.y) * 0.5 * H;
          const h = scratch.h[i];
          // 두 번째 루프의 배치와 같은 식: 행성 테두리 위 CHIP_LIFT·h 에서 시작해 가운데 정렬
          const bottom = py - (r + CHIP_LIFT * h) * pxw;
          const half = 0.5 * aspect * h * pxw;
          const o = i * 4;
          scratch.rect[o] = px - half;
          scratch.rect[o + 1] = bottom - h * pxw;
          scratch.rect[o + 2] = px + half;
          scratch.rect[o + 3] = bottom;
          // 화면 밖으로 완전히 나갔으면 그리지도, 겹침 판정에 넣지도 않는다
          if (scratch.rect[o + 2] < 0 || scratch.rect[o] > W || scratch.rect[o + 3] < 0 || scratch.rect[o + 1] > H) ct = 0;
          else {
            // 정수부 = 우선순위, 소수부 = 크기 — 같은 우선순위면 큰 행성이 앞선다
            scratch.prio[i] = prioOf(node) * 10 + node.size;
            CHIP_ORDER.push(i);
          }
        }
      }
      scratch.target[i] = ct;
    }
    cullOverlappingChips(CHIP_ORDER, scratch.rect, scratch.prio, scratch.target);
    if (discMesh) {
      discMesh.instanceMatrix.needsUpdate = true;
      built.brightAttr.needsUpdate = true;
      built.emphAttr.needsUpdate = true;
      built.alphaAttr.needsUpdate = true;
    }
    built.haloSize.needsUpdate = true;
    built.haloAlpha.needsUpdate = true;
    // 헤일로는 live.pos 를 정점 위치로 그대로 쓴다 — 모프 중이거나 드래그로 좌표가 바뀐 프레임에만 다시 올린다
    let halo = HALO_SYNC.get(built);
    if (!halo) {
      halo = { version: dragState.version };
      HALO_SYNC.set(built, halo);
    }
    // 고도·펼치기 전이는 dragState.version 을 올리지 않고 좌표만 옮긴다(간선 재생성을 피하려고) — 헤일로는 그동안 직접 따라가야 한다
    if (morphing || altitudeState.animating || spreadState.animating || halo.version !== dragState.version) {
      halo.version = dragState.version;
      built.haloPos.needsUpdate = true;
    }

    // 이름표 — 겹침 판정이 끝난 뒤 알파를 lerp 하고 행렬·속성을 쓴다
    if (chipMesh) {
      const stateArr = built.chipState.array as Float32Array;
      for (let i = 0; i < n; i += 1) {
        const node = model.nodes[i];
        const a = animFor(node.id);
        // 폭(aspect)·이름 표시(nameOn)는 첫 루프가 이 프레임 값으로 정해 두었다.
        // 내용이 없어진 이름표(호버가 떠남)는 페이드 없이 바로 접는다 — 남은 알파 동안 글자 없는 빈 캡슐이 보이지 않게
        const aspect = built.aspectArr[i];
        const ca = (a.chipA = aspect > 0 ? a.chipA + (scratch.target[i] - a.chipA) * kFast : 0);
        stateArr[i * 4] = ca;
        stateArr[i * 4 + 1] = emphasis.node.get(node.id) ?? 1;
        stateArr[i * 4 + 2] = node.id === hoveredId ? 1.1 : 1;
        stateArr[i * 4 + 3] = a.nameOn;
        if (ca < 0.01) {
          // 안 보이는 이름표는 접어 두어 레이캐스트에도 걸리지 않게 한다
          tmpObj.position.set(0, 0, 0);
          tmpObj.scale.setScalar(0.001);
        } else {
          const h = scratch.h[i];
          TMP.fromArray(live.pos, i * 3);
          TO_CAM.copy(cam.position).sub(TMP).normalize();
          const r = built.radius[i] * a.scale * live.scale[i];
          // 행성 테두리 바로 위 가운데, 원판과 같은 깊이 — 이름표는 깊이 검사 없이 맨 위에 그리므로 깊이는 투영 위치에만 쓰인다
          tmpObj.position
            .copy(TMP)
            .addScaledVector(UPV, r + CHIP_LIFT * h)
            .addScaledVector(TO_CAM, r * DISC_LIFT);
          tmpObj.scale.set(h * aspect, h, 1);
        }
        tmpObj.quaternion.copy(cam.quaternion);
        tmpObj.updateMatrix();
        chipMesh.setMatrixAt(i, tmpObj.matrix);
      }
      chipMesh.instanceMatrix.needsUpdate = true;
      built.chipAspect.needsUpdate = true;
      built.chipState.needsUpdate = true;
    }

    const rg = ring.current;
    if (rg) {
      const target = hoveredId ? model.nodeById.get(hoveredId) : undefined;
      if (target) {
        const i = model.nodes.indexOf(target);
        rg.visible = true;
        rg.position.fromArray(live.pos, i * 3);
        rg.scale.setScalar(built.radius[i] * animFor(target.id).scale * live.scale[i] * 3.0);
        const mat = rg.material as THREE.SpriteMaterial;
        mat.opacity = 0.35 + Math.sin(t * 3) * 0.08;
        mat.color.set(target.color);
        mat.rotation = t * 0.4;
      } else rg.visible = false;
    }
  });

  /* eslint-enable react-hooks/immutability */

  // 원판은 보이는 원 안, 이름표는 보이는 캡슐 안만 히트로 남긴다 (투명 모서리·접힌 이름표는 레이캐스트 단계에서 제외)
  const discRaycast = useMemo(
    () =>
      filteredRaycast((i, uv) => {
        const node = model.nodes[i];
        return !!node && animFor(node.id).tagA >= PICK_ALPHA && discHit(uv);
      }),
    [model],
  );
  const chipRaycast = useMemo(
    () =>
      filteredRaycast((i, uv) => {
        const node = model.nodes[i];
        return !!node && animFor(node.id).chipA >= PICK_ALPHA && chipHit(built.aspectArr[i], uv);
      }),
    [model, built],
  );

  // 드래그 중에는 호버 주인을 바꾸지 않는다 — 끄는 동안 이웃 강조·역할 링이 깜빡이지 않게
  const pickable = (id: string) => !morphState.plan && !dragState.id && (emphasis.node.get(id) ?? 1) > 0.05;
  /** 인스턴스 히트 → 기업 id. 원판·캡슐 판정은 raycast 래퍼가 이미 끝냈다 */
  const idOf = (e: ThreeEvent<PointerEvent | MouseEvent>) => (e.instanceId === undefined ? null : (model.nodes[e.instanceId]?.id ?? null));
  /**
   * 포인터가 빠져나갈 때 호버를 푼다 — 단, 그 노드가 현재 호버 주인일 때만.
   * pickable 이 아니어서 stopPropagation 없이 지나간 인스턴스도 R3F 는 hovered 로 등록하므로, 무조건 null 을 보내면
   * 그 뒤에서 호버 중이던 다른 행성의 강조가 끊긴다 (그 행성은 hovered 맵에 남아 다시 over 가 오지 않는다).
   */
  const release = (id: string | null) => {
    if (dragState.id) return;
    if (id && id === hoveredId) onHover(null);
  };
  const activate = (e: ThreeEvent<MouseEvent>, id: string) => {
    // 끌어서 옮긴 직후(또는 길게 눌러 워프한 직후)의 클릭은 선택·워프가 아니다
    if (dragState.blockClick) return;
    // 더블클릭은 click 두 번 + dblclick 으로 온다. 두 번째 click(detail 2)에서 먼저 워프하고, dblclick 핸들러는 detail 을 안 주는 입력 장치를 위한 보조다 (warpTo 는 워프 중 재호출을 무시한다).
    // 터치는 detail 이 1 로 고정되는 브라우저가 많아 같은 행성을 TAP_DOUBLE_MS 안에 다시 탭한 것도 더블클릭으로 본다
    const doubleTap = tapIsDouble(id);
    if ((e.nativeEvent.detail >= 2 || doubleTap) && onDoubleClick) onDoubleClick(id);
    else onClick(id);
  };
  /**
   * 길게 누르기 무장 — 터치이거나 좁은 화면(모바일 배치)이면, 행성을 HOLD_MS 동안 움직이지 않고 누르고 있을 때 관계망으로 워프한다.
   * 진행은 HUD 의 HoldRing 이 store.hold 로 그린다. 끌기 후보(armDrag)와 같은 pointerdown 에서 시작하고, 슬롭을 넘기면 끌기가 이기고 여기는 취소된다
   */
  const armHold = (e: ThreeEvent<PointerEvent>, id: string) => {
    if (!onDoubleClick) return;
    const ne = e.nativeEvent;
    if (ne.pointerType !== "touch" && !narrow) return;
    armHoldTimer(id, ne.clientX, ne.clientY, live, () => onDoubleClick(id));
  };

  /**
   * 주 버튼으로 행성·이름표를 누르면 드래그 후보로 잡아 둔다 (실제 시작은 슬롭을 넘긴 pointermove).
   * 카메라 연출 중(인트로 비행·필터 변경 재프레이밍)에는 시작하지 않는다 — 드래그 평면은 시작 시점의 시선으로 한 번만 고정되는데
   * controls.enabled=false 는 사용자 입력만 막을 뿐 Director 의 연출은 계속 카메라를 밀어 옮기므로, 같은 커서 위치가 프레임마다
   * 다른 점에 떨어져 행성이 커서에서 미끄러진다. OrbitKeys 가 같은 이유로 controls.enabled 를 보는 것과 같은 가드다.
   */
  const beginDrag = (e: ThreeEvent<PointerEvent>, id: string, index: number) => {
    const s = useGalaxy.getState();
    if (e.nativeEvent.button !== 0 || morphState.plan || s.phase === "warp" || cameraBusy.anim) return;
    if (!pickable(id)) return;
    e.stopPropagation();
    // 터치이거나 좁은 화면이면 '모바일 손짓'(길게 누르기 · 같은 행성 재탭 = 더블클릭)을 받는다 — 이 pointerdown 의 성격을 뒤따르는 click 도 본다
    armDrag(id, index, e.nativeEvent.clientX, e.nativeEvent.clientY, live, e.nativeEvent.pointerType === "touch" || narrow);
    armHold(e, id);
  };

  // 드래그 본체 — 캔버스 밖으로 나가도 끝나도록 window 에 건다
  useEffect(() => {
    /** 화면 좌표 → 드래그 평면 위의 점. 평면은 시작할 때 노드를 지나며 시선에 수직으로 고정한다 */
    const hitPlane = (x: number, y: number) => {
      const r = gl.domElement.getBoundingClientRect();
      PTR.set(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1);
      RAY.setFromCamera(PTR, camera);
      return RAY.ray.intersectPlane(DRAG_PLANE, DRAG_HIT);
    };
    const start = () => {
      DRAG.active = true;
      // dirty 는 소비자가 여럿이라 여기서만 비운다 (morph.ts 주석 참고)
      dragState.dirty.clear();
      if (DRAG.id) dragState.dirty.add(DRAG.id);
      DRAG_POS.fromArray(live.pos, DRAG.index * 3);
      // 관성 상태 준비 — 목표는 현재 자리, 속도 0 (이전 글라이드가 남아 있으면 이 노드로 넘어온다)
      GLIDE.live = live;
      GLIDE.index = DRAG.index;
      GLIDE.active = true;
      GLIDE.origin.copy(DRAG_POS);
      GLIDE.target.copy(DRAG_POS);
      GLIDE.vel.set(0, 0, 0);
      camera.getWorldDirection(DRAG_DIR);
      DRAG_PLANE.setFromNormalAndCoplanarPoint(DRAG_DIR, DRAG_POS);
      // 잡은 지점(누른 자리)을 기준으로 오프셋을 잡는다 — 행성이 커서 아래로 순간이동하지도, 슬롭만큼 뒤처지지도 않게
      if (hitPlane(DRAG.x, DRAG.y)) DRAG_OFF.subVectors(DRAG_HIT, DRAG_POS);
      else DRAG_OFF.set(0, 0, 0);
      DRAG.cursor = document.body.style.cursor;
      document.body.style.cursor = "grabbing";
    };
    const onMove = (ev: PointerEvent) => {
      // 길게 누르기는 손가락이 슬롭을 넘기면 취소 — 그 뒤는 끌기다
      if (HOLD.id && Math.hypot(ev.clientX - DRAG.x, ev.clientY - DRAG.y) >= DRAG_SLOP) clearHold();
      if (!DRAG.id || DRAG.live !== live) return;
      if (!DRAG.active) {
        if (Math.hypot(ev.clientX - DRAG.x, ev.clientY - DRAG.y) < DRAG_SLOP) return;
        start();
      }
      if (!hitPlane(ev.clientX, ev.clientY)) return;
      // 커서가 가리키는 자리와 시작 자리 사이의 일부만 목표로 삼는다 — 실제 이동은 useFrame 의 스프링이 맡는다
      DRAG_HIT.sub(DRAG_OFF).sub(GLIDE.origin).multiplyScalar(DRAG_SENS);
      GLIDE.target.copy(GLIDE.origin).add(DRAG_HIT);
    };
    const onUp = () => {
      clearHold();
      if (HOLD.fired) {
        // 길게 눌러 워프한 뒤 손을 떼면 click 이 한 번 따라온다 — 그 클릭이 미리보기를 열지 않게 삼킨다 (끌기 뒤 처리와 같은 방식)
        HOLD.fired = false;
        dragState.blockClick = true;
        requestAnimationFrame(() => {
          dragState.blockClick = false;
        });
      }
      if (DRAG.live !== live) return;
      if (DRAG.active) {
        document.body.style.cursor = DRAG.cursor;
        // 뒤따라오는 click 한 번만 삼킨다 (click 은 pointerup 과 같은 태스크에서 오고 rAF 는 그 뒤다)
        dragState.blockClick = true;
        requestAnimationFrame(() => {
          dragState.blockClick = false;
        });
        // 포인터가 행성 밖에서 손을 뗐을 수 있다 — 호버를 풀면 다음 포인터 이동이 알아서 다시 잡는다
        onHover(null);
      }
      DRAG.id = null;
      DRAG.active = false;
      DRAG.live = null;
      dragState.id = null;
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    window.addEventListener("blur", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      window.removeEventListener("blur", onUp);
      // 누르고 있는 도중 언마운트되면(라우트 이동·세션 리다이렉트) 이 인스턴스가 건 길게 누르기 타이머를 버린다 — 남겨 두면 사라진 트리에서 잡은 기업으로 뒤늦게 warpTo 가 간다
      if (HOLD.live === live) clearHold();
      // 끄는 도중 언마운트되면(라우트 이동) 커서·컨트롤 잠금만 되돌린다 — 여기서 호버를 건드리면 사라진 트리에 set 이 간다
      if (DRAG.live !== live) return;
      if (DRAG.active) document.body.style.cursor = DRAG.cursor;
      DRAG.id = null;
      DRAG.active = false;
      DRAG.live = null;
      dragState.id = null;
    };
  }, [gl, camera, live, onHover]);

  return (
    <group>
      {/* 로고 행성 — 드로우콜 1회. 투명 블렌딩(테두리 안티에일리어싱)을 쓰면서도 예전 구체처럼 깊이를 써서, 관계선(renderOrder 1·2)보다
          먼저 그려 행성 뒤로 지나가는 선을 가린다 */}
      <instancedMesh
        ref={discs}
        args={[built.geo, undefined, n]}
        frustumCulled={false}
        renderOrder={-1}
        raycast={discRaycast}
        onPointerMove={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id)) return;
          e.stopPropagation();
          onHover(id);
        }}
        onPointerOut={(e) => release(idOf(e))}
        onPointerDown={(e) => {
          const id = idOf(e);
          if (id && e.instanceId !== undefined) beginDrag(e, id, e.instanceId);
        }}
        onClick={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id)) return;
          e.stopPropagation();
          activate(e, id);
        }}
        onDoubleClick={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id) || dragState.blockClick) return;
          e.stopPropagation();
          onDoubleClick?.(id);
        }}
      >
        <shaderMaterial vertexShader={discVertex} fragmentShader={discFragment} uniforms={discUniforms} transparent depthWrite toneMapped={false} />
      </instancedMesh>

      {/* 이름표 — 호버·선택한 기업의 이름과 이웃의 역할 태그, 드로우콜 1회. 몇 개 안 뜨는 대신 앞의 행성·관계선 라벨에 가리지 않게 맨 위에 그린다.
          히트는 캡슐 안으로 제한되므로 onPointerOut 이 곧 캡슐 이탈이다 */}
      <instancedMesh
        ref={chips}
        args={[built.chipGeo, undefined, n]}
        frustumCulled={false}
        renderOrder={5}
        raycast={chipRaycast}
        onPointerMove={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id)) return;
          e.stopPropagation();
          onHover(id);
        }}
        onPointerOut={(e) => release(idOf(e))}
        onPointerDown={(e) => {
          const id = idOf(e);
          if (id && e.instanceId !== undefined) beginDrag(e, id, e.instanceId);
        }}
        onClick={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id)) return;
          e.stopPropagation();
          activate(e, id);
        }}
        onDoubleClick={(e) => {
          const id = idOf(e);
          if (!id || !pickable(id) || dragState.blockClick) return;
          e.stopPropagation();
          onDoubleClick?.(id);
        }}
      >
        <shaderMaterial vertexShader={chipVertex} fragmentShader={chipFragment} uniforms={chipUniforms} transparent depthWrite={false} depthTest={false} toneMapped={false} />
      </instancedMesh>

      <points geometry={built.haloGeo} frustumCulled={false} raycast={() => null} renderOrder={1}>
        <shaderMaterial
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          vertexColors
          toneMapped={false}
          uniforms={haloUniforms}
          vertexShader={/* glsl */ `
            attribute float aSize;
            attribute float aAlpha;
            varying vec3 vColor;
            varying float vAlpha;
            void main(){
              vColor = color; vAlpha = aAlpha;
              vec4 mv = modelViewMatrix * vec4(position, 1.0);
              gl_PointSize = aSize * (300.0 / -mv.z);
              gl_Position = projectionMatrix * mv;
            }
          `}
          fragmentShader={/* glsl */ `
            uniform sampler2D uMap;
            varying vec3 vColor;
            varying float vAlpha;
            void main(){
              vec4 t = texture2D(uMap, gl_PointCoord);
              gl_FragColor = vec4(vColor * t.a, t.a * vAlpha);
            }
          `}
        />
      </points>
      <sprite ref={ring} visible={false} raycast={() => null} renderOrder={4}>
        <spriteMaterial map={ringTexture()} transparent depthWrite={false} toneMapped={false} blending={THREE.AdditiveBlending} />
      </sprite>
    </group>
  );
}
