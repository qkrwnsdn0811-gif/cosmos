import { useEffect, useMemo, useRef } from "react";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneModel, SceneNode } from "@/lib/graph";
import { initials } from "@/lib/format";
import { logoUrl } from "@/lib/logos";
import { hashString } from "@/lib/rng";
import { ROLE_META, ROLE_ORDER, roleColor, type RoleKey } from "@/lib/roles";
import { useGalaxy } from "@/store/galaxy";
import { altitudeState } from "./altitude";
import { touch } from "./bufferUtil";
import { cameraBusy } from "./cameraState";
import { CHIP_H, CHIP_PILL, CHIP_STEM, chipFragment, chipHit, chipVertex } from "./chipShader";
import type { Emphasis } from "./emphasis";
import { ATLAS_CELL_UV, cellUv, ensureLogoCell, logoAtlasTexture } from "./logoAtlas";
import { discFragment, discVertex } from "./logoDiscShader";
import { dragState, liveFor, morphState, type LiveTransform } from "./morph";
import { ensureNameCell, nameAtlasTexture, type NameCell } from "./nameAtlas";
import { animFor } from "./nodeAnim";
import { dotTexture, ringTexture } from "./textures";
import { planetGeometry, planetPaletteFor, toonGradient, type PlanetPalette } from "./toon";

interface Props {
  model: SceneModel;
  emphasis: Emphasis;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  onClick: (id: string) => void;
  /** 이름표 칩에 기업명을 펼칠지 (끄면 로고 원만 남는다). 강조로 고정된 노드는 항상 펼친다 */
  showLabels?: boolean;
  /** 더블클릭(두 번째 click, nativeEvent.detail ≥ 2) — 그 기업의 관계망으로. 첫 클릭은 onClick(미리보기)으로 이미 처리돼 있다 */
  onDoubleClick?: (id: string) => void;
  /** 호버 노드의 이웃 → 역할. 그 이웃 칩의 로고 원과 이름 사이에 유형색 역할 태그(공급사·고객…)가 붙는다 */
  roleTags?: Map<string, RoleKey> | null;
}

type Variant = "land" | "ring" | "moon";
interface Planet {
  node: SceneNode;
  radius: number;
  palette: PlanetPalette;
  variant: Variant;
  seed: number;
  geo: THREE.BufferGeometry;
  moonGeo: THREE.BufferGeometry | null;
}

const tmpObj = new THREE.Object3D();
const TMP = new THREE.Vector3();
const TO_CAM = new THREE.Vector3();
const UPV = new THREE.Vector3();
const RGT = new THREE.Vector3();
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
const DRAG = { id: null as string | null, index: -1, x: 0, y: 0, active: false, cursor: "", live: null as LiveTransform | null };
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
function armDrag(id: string, index: number, x: number, y: number, live: LiveTransform) {
  DRAG.id = id;
  DRAG.index = index;
  DRAG.x = x;
  DRAG.y = y;
  DRAG.active = false;
  DRAG.live = live;
  dragState.id = id;
  // 앞선 드래그의 클릭 차단이 남아 있을 일은 없지만(rAF 에서 풀린다), 새 입력이 시작되면 확실히 턴다
  dragState.blockClick = false;
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
 * 티어별 칩이 완전히 사라지는 카메라 거리 — 0.75 배 지점부터 페이드. 허브는 은하 기본 자세에서도 보인다.
 * 은하: 기본 자세(poseFor, 188개 목업 기준 카메라 거리 ≈ 250~290, 30° 부감·주가 고도 켬)에서 tier 1(질량 랭크 12~49)이
 *       가장 가까워도 ≈ 200 이므로 tier 1 상한을 140 으로 두어 '기본 자세에서는 허브만' 을 지킨다 (페이드 105~140).
 * 기업 중심: 기본 자세(거리 ≈ 118)에서 depth 1 궤도가 96~142 에 걸치므로 200 을 유지해야 첫 궤도 칩이 다 보인다.
 */
const TIER_FAR: Record<SceneModel["kind"], number[]> = { galaxy: [520, 140, 110], system: [520, 200, 110] };
/**
 * 칩 겹침 판정의 여백(px) — 화면에 실제로 그려질 칩 사각형이 이 여백 안으로 겹치면 우선순위 낮은 쪽을 숨긴다.
 * 예전 방식(행성 위치를 96×28 격자에 넣어 같은 칸만 겹침으로 침)은 칩이 칸보다 넓어지자 무력해졌다 — 26px 칩은 폭이 100~150px 라
 * 옆 칸의 칩과 그대로 겹쳤다(기본 자세에서 겹치는 쌍 실측 4.3/프레임). 사각형끼리 재면 폭이 얼마든 정확하다
 */
const CHIP_GAP = 4;
/**
 * 칩 목표 화면 높이(px)와 월드 높이 클램프 — 코앞에서도 너무 작아지지 않고 멀리서도 행성을 통째로 덮지 않게.
 * 20px·상한 4 일 때는 은하 기본 자세에서 칩이 13px 안팎, 글자는 7px 남짓이라 읽히지 않았다. 26px 로 키우고 상한을 10 으로 올렸다.
 * 상한이 걸리는 구간(기본 자세 카메라 거리 ≈ 265~310 에서 계산): 캔버스 세로 790px 이상이면 허브 칩이 모두 26px, 720px 에서는 23.8~26px,
 * 650px 에서는 21.5~25.2px. 글자 높이는 이름 아틀라스 행(ROW 44 중 글자 36)에 따라 칩 높이의 약 0.65 배 ≈ 17px 이다.
 * 하한은 코앞(궤도 최소 거리 22)에서 칩이 화면 45~65px 로 커지는 정도 — 더 올리면 긴 이름이 화면 1/3 을 덮는다
 */
const CHIP_PX = 26;
const CHIP_MIN = 1.3;
const CHIP_MAX = 10.0;
/** 항성 점광 레이어 — 랩과 동일 */
const LIT = 1;
/** 피킹에서 무시하는 배지·칩 알파 — 이 아래는 보이지 않는데 포인터를 가로채면 안 된다 */
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
 * 기업 노드 = 저폴리 툰 행성 (장난감 우주 스타일).
 * - 산업색에서 만든 팔레트로 면 단위 바다·대륙을 칠하고, 대륙형·고리형·위성형을 기업 id 시드로 섞는다
 *   (은하 뷰와 기업 중심 뷰에서 같은 기업이 같은 행성으로 보인다)
 * - 크기는 연결 가중치 + 시가총액(node.size, graph.ts nodeSize), 호버·중심·강조로 크기와 밝기를 프레임마다 lerp. 위치는 live 버퍼(morph.ts)에서 읽는다
 * - 행성 표면은 비워 두고, 로고·기업명은 행성 위 깃발 칩(InstancedMesh 1회)에 놓는다. 칩은 화면상 약 26px 높이로
 *   항상 카메라를 향하고, 티어(허브·첫 껍질·나머지)별 거리 LOD 와 칩 사각형 겹침 판정(cullOverlappingChips)으로 혼잡을 억제한다
 * - 정면 배지(흰 로고 원판)는 emphasis.badges — 호버·중심·선택 간선 양끝 — 에서만 카메라→행성 시선 위에 떠오른다
 * - 멀리서도 위치가 보이도록 아주 약한 헤일로 점광을 함께 둔다
 * - 행성·칩·배지를 끌면 그 행성을 옮길 수 있다. 옮긴 자리는 live 버퍼에만 남으므로 새로고침하면 레이아웃 좌표로 돌아간다
 */
export default function CompanyNodes({ model, emphasis, hoveredId, onHover, onClick, showLabels = true, onDoubleClick, roleTags = null }: Props) {
  const tags = useRef<THREE.InstancedMesh>(null);
  const chips = useRef<THREE.InstancedMesh>(null);
  const ring = useRef<THREE.Sprite>(null);
  const groups = useRef<(THREE.Group | null)[]>([]);
  const bodies = useRef<(THREE.Mesh | null)[]>([]);
  const moons = useRef<(THREE.Group | null)[]>([]);
  const n = model.nodes.length;
  const live = liveFor(model);
  const gl = useThree((s) => s.gl);
  const camera = useThree((s) => s.camera);

  const planets = useMemo<Planet[]>(
    () =>
      model.nodes.map((node) => {
        // 배열 인덱스가 아니라 기업 id 로 시드를 만들어 뷰가 바뀌어도 외형이 유지된다
        const h = (hashString(node.id) % 1000) / 1000;
        const variant: Variant = h < 0.15 ? "ring" : h < 0.27 ? "moon" : "land";
        const radius = node.size * 1.3;
        const palette = planetPaletteFor(node.color);
        const seed = 0.5 + h * 9;
        // 세분화는 반지름과 무관하게 고정한다 — 뷰마다 size 가 달라도(기업 중심의 중심 3.4 등) 같은 면 분할·대륙 모양이 나오도록.
        // 188개 × 320면이라 비용은 무시할 수준
        const geo = planetGeometry(radius, palette, seed, 2, variant === "ring" ? 0.3 : 0.45);
        const moonGeo = variant === "moon" ? planetGeometry(radius * 0.3, palette, seed + 5, 1, 0.2) : null;
        return { node, radius, palette, variant, seed, geo, moonGeo };
      }),
    [model],
  );
  useEffect(
    () => () => {
      planets.forEach((p) => {
        p.geo.dispose();
        p.moonGeo?.dispose();
      });
    },
    [planets],
  );

  // 모프 없는 모델 교체(필터·폴백)는 기존처럼 팝인하고, 워프 모프 중 마운트는 이어받은 상태를 보존한다
  useEffect(() => {
    if (morphState.plan) return;
    model.nodes.forEach((node) => {
      animFor(node.id).scale = 0.001;
    });
  }, [model]);

  // 배지·칩·헤일로 버퍼
  const built = useMemo(() => {
    const color = new Float32Array(n * 3);
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
      size[i] = node.size;
      center.x += node.pos[0];
      center.y += node.pos[1];
      center.z += node.pos[2];
      const idx = ensureLogoCell(`${node.stockCode}|${node.name}`, initials(node.name), node.color, logoUrl(node.stockCode));
      const [u, v] = cellUv(idx);
      cell[i * 2] = u;
      cell[i * 2 + 1] = v;
      const nc = ensureNameCell(node.name);
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

    const geo = new THREE.PlaneGeometry(2, 2);
    geo.setAttribute("aColor", new THREE.InstancedBufferAttribute(color, 3));
    geo.setAttribute("aCell", new THREE.InstancedBufferAttribute(cell, 2));
    const brightAttr = dyn(bright, 1);
    const emphAttr = dyn(emph, 1);
    const alphaAttr = dyn(alpha, 1);
    geo.setAttribute("aBright", brightAttr);
    geo.setAttribute("aEmph", emphAttr);
    geo.setAttribute("aAlpha", alphaAttr);

    // 칩: 로컬 y ∈ [-0.35, 1] (아래 0.35 가 스템). x 폭은 인스턴스 행렬로 aspect 배 늘린다
    const chipGeo = new THREE.PlaneGeometry(1, CHIP_H);
    chipGeo.translate(0, CHIP_H / 2 - CHIP_STEM, 0);
    chipGeo.setAttribute("aColor", new THREE.InstancedBufferAttribute(color, 3));
    chipGeo.setAttribute("aCell", new THREE.InstancedBufferAttribute(cell, 2));
    chipGeo.setAttribute("aName", new THREE.InstancedBufferAttribute(name, 4));
    const aspectArr = new Float32Array(n).fill(1);
    const chipAspect = dyn(aspectArr, 1);
    // (alpha, emph, bright, nameOn) 을 vec4 하나로 — 정점 속성 수를 16 개 한도 아래로 유지한다 (chipShader 주석 참고)
    const chipStateArr = new Float32Array(n * 4);
    for (let i = 0; i < n; i += 1) {
      chipStateArr[i * 4 + 1] = 1;
      chipStateArr[i * 4 + 2] = 1;
      chipStateArr[i * 4 + 3] = 1;
    }
    const chipState = dyn(chipStateArr, 4);
    const chipRole = dyn(new Float32Array(n * 4), 4);
    const chipRoleColor = dyn(new Float32Array(n * 3), 3);
    chipGeo.setAttribute("aAspect", chipAspect);
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
    return { geo, brightAttr, emphAttr, alphaAttr, chipGeo, chipAspect, chipState, chipRole, chipRoleColor, aspectArr, nameCells, bounds, haloGeo, haloPos, haloSize, haloAlpha };
  }, [model, n]);
  useEffect(
    () => () => {
      built.geo.dispose();
      built.chipGeo.dispose();
      built.haloGeo.dispose();
    },
    [built],
  );

  // 역할 태그 — 호버가 바뀔 때만 이웃 칩의 aRole(이름 아틀라스의 역할 라벨 셀)·aRoleColor 를 쓴다. 폭(roleW)은 프레임 루프가 aRole 에서 읽는다
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

  // 프레임 스크래치 — 칩 높이·목표 알파·화면 사각형(left, top, right, bottom)·우선순위 (겹침 판정이 끝난 뒤 두 번째 루프에서 쓴다)
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
    init(tags.current, { pick: "node" });
    init(chips.current, { pick: "node" });
  }, [model, n, built]);

  const discUniforms = useMemo(() => ({ uAtlas: { value: logoAtlasTexture() }, uCell: { value: ATLAS_CELL_UV } }), []);
  const chipUniforms = useMemo(
    () => ({ uAtlas: { value: logoAtlasTexture() }, uCell: { value: ATLAS_CELL_UV }, uNames: { value: nameAtlasTexture() }, uPill: { value: new THREE.Color(CHIP_PILL) } }),
    [],
  );
  const haloUniforms = useMemo(() => ({ uMap: { value: dotTexture() } }), []);
  const grad = toonGradient(3);

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
    // 배지·칩 페이드는 더 빠르게 (0.3s 안에 거의 끝난다)
    const kFast = 1 - Math.pow(0.0001, dt);
    const t = state.clock.elapsedTime;
    const cam = state.camera;
    const tagMesh = tags.current;
    const chipMesh = chips.current;
    const morphing = morphState.plan !== null;

    // 칩 배치 기준: 카메라 상·우 방향, 화면 CHIP_PX 에 해당하는 월드 높이 계수
    UPV.set(0, 1, 0).applyQuaternion(cam.quaternion);
    RGT.set(1, 0, 0).applyQuaternion(cam.quaternion);
    const persp = cam as THREE.PerspectiveCamera;
    const fov = persp.isPerspectiveCamera ? persp.fov : 52;
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    const pxK = (2 * tanHalf * CHIP_PX) / Math.max(1, state.size.height);
    const W = state.size.width;
    const H = state.size.height;
    const tierFar = TIER_FAR[model.kind];
    // 세로로 긴 캔버스는 poseFor 가 가로를 맞추느라 카메라를 1/종횡비 만큼 물린다 — 티어 거리도 같은 배율로 늘려야
    // 좁은 창(모바일 세로)에서 허브 이름표가 페이드에 걸려 사라지지 않는다
    const narrow = Math.max(1, 1 / Math.max(0.2, Math.min(1, W / Math.max(1, H))));
    // 겹침 우선순위: 호버 > 중심 기업 > 강조 고정 > 낮은 티어. 호버·중심에 별도 등급이 없으면 겹친 큰 pinned 이웃이
    // 호버한 기업의 칩을 밀어내 숨긴다
    const prioOf = (nd: SceneNode) => (nd.id === hoveredId ? 5 : nd.id === model.centerId ? 4 : emphasis.pinned.has(nd.id) ? 3 : 2 - nd.tier);
    CHIP_ORDER.length = 0;
    const roleArr = built.chipRole.array as Float32Array;

    planets.forEach((p, i) => {
      const { node } = p;
      const a = animFor(node.id);
      const emph = emphasis.node.get(node.id) ?? 1;
      const isHover = node.id === hoveredId;
      const isCenter = node.id === model.centerId;
      const pinned = emphasis.pinned.has(node.id);
      const targetScale = (isHover ? 1.18 : 1) * (isCenter ? 1.1 : 1) * (0.7 + 0.3 * emph);
      const targetBright = (isHover ? 1.15 : isCenter ? 1.06 : 1.0) * (0.42 + 0.58 * emph);
      const s = (a.scale += (targetScale - a.scale) * k);
      const br = (a.bright += (targetBright - a.bright) * k);

      TMP.fromArray(live.pos, i * 3);
      const g = groups.current[i];
      if (g) {
        g.position.copy(TMP);
        g.scale.setScalar(s * live.scale[i]);
      }
      const body = bodies.current[i];
      if (body) {
        body.rotation.y = t * 0.12 + p.seed;
        (body.material as THREE.MeshToonMaterial).color.setScalar(br);
      }
      const moon = moons.current[i];
      if (moon) moon.rotation.y = t * 0.5 + p.seed;

      TO_CAM.copy(cam.position).sub(TMP);
      const dist = TO_CAM.length();
      TO_CAM.multiplyScalar(1 / Math.max(1e-6, dist));
      const r = p.radius * s * live.scale[i];

      // 정면 배지: badges 에 든 노드만. 행성 중심에서 카메라 쪽으로 반지름만큼 나온 지점에 빌보드로 놓아 항상 한가운데
      const tagTarget = emphasis.badges.has(node.id) && (!morphing || isCenter) ? 1 : 0;
      const ta = (a.tagA += (tagTarget - a.tagA) * kFast);
      if (tagMesh) {
        tmpObj.position.copy(TMP).addScaledVector(TO_CAM, r * 1.04);
        tmpObj.quaternion.copy(cam.quaternion);
        tmpObj.scale.setScalar(ta < 0.01 ? 0.001 : r * 0.62 * (0.6 + 0.4 * ta));
        tmpObj.updateMatrix();
        tagMesh.setMatrixAt(i, tmpObj.matrix);
        (built.brightAttr.array as Float32Array)[i] = isHover ? 1.05 : 1;
        (built.emphAttr.array as Float32Array)[i] = emph;
        (built.alphaAttr.array as Float32Array)[i] = ta;
      }
      (built.haloSize.array as Float32Array)[i] = r * 3.2 * (0.5 + 0.5 * emph);
      (built.haloAlpha.array as Float32Array)[i] = (0.15 + 0.85 * emph) * 0.22;

      // 칩 폭(aspect)은 여기서 이 프레임 값으로 확정한다 — 겹침 판정(아래)과 배치(두 번째 루프)가 같은 폭을 봐야 한다.
      // 역할 태그 폭은 호버 순간 0 → 전체로 바로 뛰므로 직전 프레임 값을 쓰면 그 프레임에 이웃 칩과 겹친다
      a.nameOn += ((showLabels || pinned ? 1 : 0) - a.nameOn) * kFast;
      const roleW = roleArr[i * 4 + 3] > 0 ? (roleArr[i * 4 + 2] / roleArr[i * 4 + 3]) * 0.8 : 0;
      built.aspectArr[i] = 1 + a.nameOn * ((built.nameCells[i]?.aspect ?? 0) * 0.8 + 0.3) + (roleW > 0 ? roleW + 0.15 : 0);

      // 칩 목표 알파: 티어별 거리 LOD × 강조, 고정 노드는 항상. 모프 중에는 중심만
      scratch.h[i] = THREE.MathUtils.clamp(dist * pxK, CHIP_MIN, CHIP_MAX);
      const far = tierFar[Math.min(node.tier, tierFar.length - 1)] * narrow;
      let ct = pinned ? 1 : 1 - THREE.MathUtils.smoothstep(dist, far * 0.75, far);
      ct *= 0.15 + 0.85 * emph;
      if (morphing && !isCenter) ct = 0;
      if (ct > 0.01) {
        // 카메라 뒤는 제외. 앞이면 칩이 그려질 화면 사각형을 재어 두고, 루프가 끝난 뒤 우선순위 순으로 겹침을 걸러낸다
        // 두 번째 루프의 배치처럼 카메라 쪽으로 r·0.2 만큼 뺀 지점을 투영한다 (화면 가장자리에서는 이 오프셋이 몇 px 옆으로 새기 때문)
        NDC.copy(TMP).addScaledVector(TO_CAM, r * 0.2).applyMatrix4(cam.matrixWorldInverse);
        if (NDC.z >= -1e-3) ct = 0;
        else {
          // 이 깊이에서 월드 1 이 차지하는 픽셀 — 빌보드라 화면 크기는 여기서 그대로 정해진다
          const pxw = H / (2 * -NDC.z * tanHalf);
          NDC.applyMatrix4(cam.projectionMatrix);
          const px = (NDC.x + 1) * 0.5 * W;
          const py = (1 - NDC.y) * 0.5 * H;
          const h = scratch.h[i];
          // 두 번째 루프의 배치와 같은 식: 로고 원(폭 h)이 행성 바로 위, 캡슐은 오른쪽으로 aspect 만큼
          const aspect = built.aspectArr[i];
          const bottom = py - (r * 1.05 + CHIP_STEM * h) * pxw;
          const o = i * 4;
          scratch.rect[o] = px - 0.5 * h * pxw;
          scratch.rect[o + 1] = bottom - h * pxw;
          scratch.rect[o + 2] = px + (aspect - 0.5) * h * pxw;
          scratch.rect[o + 3] = bottom;
          // 정수부 = 우선순위, 소수부 = 크기(≤ 3.5) — 같은 우선순위면 큰 행성이 앞선다
          scratch.prio[i] = prioOf(node) * 10 + node.size;
          CHIP_ORDER.push(i);
        }
      }
      scratch.target[i] = ct;
    });
    cullOverlappingChips(CHIP_ORDER, scratch.rect, scratch.prio, scratch.target);
    if (tagMesh) {
      tagMesh.instanceMatrix.needsUpdate = true;
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
    // 고도 전이는 dragState.version 을 올리지 않고 y 만 옮긴다(간선 재생성을 피하려고) — 헤일로는 그동안 직접 따라가야 한다
    if (morphing || altitudeState.animating || halo.version !== dragState.version) {
      halo.version = dragState.version;
      built.haloPos.needsUpdate = true;
    }

    // 칩 — 겹침 판정이 끝난 뒤 알파를 lerp 하고 행렬·속성을 쓴다
    if (chipMesh) {
      const stateArr = built.chipState.array as Float32Array;
      for (let i = 0; i < n; i += 1) {
        const p = planets[i];
        const { node } = p;
        const a = animFor(node.id);
        const ca = (a.chipA += (scratch.target[i] - a.chipA) * kFast);
        // 폭(aspect)·이름 표시(nameOn)는 첫 루프가 이 프레임 값으로 정해 두었다
        const aspect = built.aspectArr[i];
        stateArr[i * 4] = ca;
        stateArr[i * 4 + 1] = emphasis.node.get(node.id) ?? 1;
        stateArr[i * 4 + 2] = node.id === hoveredId ? 1.1 : 1;
        stateArr[i * 4 + 3] = a.nameOn;
        if (ca < 0.01) {
          // 안 보이는 칩은 접어 두어 레이캐스트에도 걸리지 않게 한다
          tmpObj.position.set(0, 0, 0);
          tmpObj.scale.setScalar(0.001);
        } else {
          const h = scratch.h[i];
          TMP.fromArray(live.pos, i * 3);
          TO_CAM.copy(cam.position).sub(TMP).normalize();
          const r = p.radius * a.scale * live.scale[i];
          // 로고 원(x=0.5)이 행성 바로 위에 오도록 캡슐 중심을 오른쪽으로 밀고, 살짝 카메라 쪽으로 빼 행성에 가리지 않게 한다
          tmpObj.position
            .copy(TMP)
            .addScaledVector(UPV, r * 1.05 + CHIP_STEM * h)
            .addScaledVector(RGT, (aspect * 0.5 - 0.5) * h)
            .addScaledVector(TO_CAM, r * 0.2);
          tmpObj.quaternion.copy(cam.quaternion);
          tmpObj.scale.set(h * aspect, h, 1);
        }
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
        rg.scale.setScalar(planets[i].radius * animFor(target.id).scale * live.scale[i] * 3.0);
        const mat = rg.material as THREE.SpriteMaterial;
        mat.opacity = 0.35 + Math.sin(t * 3) * 0.08;
        mat.color.set(target.color);
        mat.rotation = t * 0.4;
      } else rg.visible = false;
    }
  });

  /* eslint-enable react-hooks/immutability */

  // 배지는 보이는 원판 안, 칩은 보이는 캡슐 안만 히트로 남긴다 (스템·투명 모서리·접힌 칩은 레이캐스트 단계에서 제외)
  const tagRaycast = useMemo(
    () =>
      filteredRaycast((i, uv) => {
        const node = model.nodes[i];
        return !!node && animFor(node.id).tagA >= PICK_ALPHA && Math.hypot(uv.x - 0.5, uv.y - 0.5) <= 0.5;
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
    // 끌어서 옮긴 직후의 클릭은 선택·워프가 아니다
    if (dragState.blockClick) return;
    // 더블클릭은 click 두 번 + dblclick 으로 온다. 두 번째 click(detail 2)에서 먼저 워프하고, dblclick 핸들러는 detail 을 안 주는 입력 장치를 위한 보조다 (warpTo 는 워프 중 재호출을 무시한다)
    if (e.nativeEvent.detail >= 2 && onDoubleClick) onDoubleClick(id);
    else onClick(id);
  };

  /**
   * 주 버튼으로 행성·칩·배지를 누르면 드래그 후보로 잡아 둔다 (실제 시작은 슬롭을 넘긴 pointermove).
   * 카메라 연출 중(인트로 비행·필터 변경 재프레이밍)에는 시작하지 않는다 — 드래그 평면은 시작 시점의 시선으로 한 번만 고정되는데
   * controls.enabled=false 는 사용자 입력만 막을 뿐 Director 의 연출은 계속 카메라를 밀어 옮기므로, 같은 커서 위치가 프레임마다
   * 다른 점에 떨어져 행성이 커서에서 미끄러진다. OrbitKeys 가 같은 이유로 controls.enabled 를 보는 것과 같은 가드다.
   */
  const beginDrag = (e: ThreeEvent<PointerEvent>, id: string, index: number) => {
    const s = useGalaxy.getState();
    if (e.nativeEvent.button !== 0 || morphState.plan || s.phase === "warp" || cameraBusy.anim) return;
    if (!pickable(id)) return;
    e.stopPropagation();
    armDrag(id, index, e.nativeEvent.clientX, e.nativeEvent.clientY, live);
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
      {planets.map((p, i) => (
        <group
          key={p.node.id}
          ref={(el) => {
            groups.current[i] = el;
          }}
          position={p.node.pos}
          scale={0.001}
        >
          <mesh
            ref={(el) => {
              bodies.current[i] = el;
            }}
            geometry={p.geo}
            onUpdate={(m) => {
              m.layers.enable(LIT);
              m.userData.pick = "node";
            }}
            onPointerOver={(e) => {
              if (!pickable(p.node.id)) return;
              e.stopPropagation();
              onHover(p.node.id);
            }}
            onPointerOut={() => release(p.node.id)}
            onPointerDown={(e) => beginDrag(e, p.node.id, i)}
            onClick={(e) => {
              if (!pickable(p.node.id)) return;
              e.stopPropagation();
              activate(e, p.node.id);
            }}
            onDoubleClick={(e) => {
              if (!pickable(p.node.id) || dragState.blockClick) return;
              e.stopPropagation();
              onDoubleClick?.(p.node.id);
            }}
          >
            <meshToonMaterial vertexColors gradientMap={grad} />
          </mesh>
          {p.variant === "ring" && (
            <mesh rotation={[Math.PI / 2 - 0.42, 0.25, 0]} raycast={() => null} onUpdate={(m) => m.layers.enable(LIT)}>
              <ringGeometry args={[p.radius * 1.45, p.radius * 2.05, 40, 1]} />
              <meshToonMaterial color={p.palette.ring} gradientMap={grad} side={THREE.DoubleSide} transparent opacity={0.9} />
            </mesh>
          )}
          {p.variant === "moon" && p.moonGeo && (
            <group
              ref={(el) => {
                moons.current[i] = el;
              }}
              rotation={[0.3, 0, 0.15]}
            >
              <mesh geometry={p.moonGeo} position={[p.radius * 2.0, 0, 0]} raycast={() => null} onUpdate={(m) => m.layers.enable(LIT)}>
                <meshToonMaterial vertexColors gradientMap={grad} />
              </mesh>
            </group>
          )}
        </group>
      ))}

      {/* 정면 배지 — 호버·중심·선택 간선 양끝에만, 드로우콜 1회 */}
      <instancedMesh
        ref={tags}
        args={[built.geo, undefined, n]}
        frustumCulled={false}
        renderOrder={3}
        raycast={tagRaycast}
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
        <shaderMaterial vertexShader={discVertex} fragmentShader={discFragment} uniforms={discUniforms} transparent depthWrite={false} toneMapped={false} />
      </instancedMesh>

      {/* 이름표 칩 — 로고 원 + 기업명, 드로우콜 1회. 행성보다 앞이면 그려지고 다른 행성 뒤면 가려진다.
          히트는 캡슐 안으로 제한되므로 onPointerOut 이 곧 캡슐 이탈이다 */}
      <instancedMesh
        ref={chips}
        args={[built.chipGeo, undefined, n]}
        frustumCulled={false}
        renderOrder={4}
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
        <shaderMaterial vertexShader={chipVertex} fragmentShader={chipFragment} uniforms={chipUniforms} transparent depthWrite={false} depthTest toneMapped={false} />
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
