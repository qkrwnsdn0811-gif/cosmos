import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneModel } from "@/lib/graph";
import { ROLE_META, roleColor, type RoleKey } from "@/lib/roles";
import { useGalaxy } from "@/store/galaxy";
import { touch } from "./bufferUtil";
import { liveFor, morphState } from "./morph";
import { animFor } from "./nodeAnim";

interface Props {
  model: SceneModel;
  hoveredId: string | null;
  /** 호버 노드의 이웃 → 역할 (Scene 이 필터를 적용해 계산, 칩 태그와 같은 맵) */
  roles: Map<string, RoleKey> | null;
}

/** 한 번에 그릴 이웃 링 상한 — 허브도 1홉 이웃이 64 를 넘는 경우는 없다 (넘으면 점수순 앞부분만) */
const MAX = 64;
/** 링 반지름(월드) = 행성 반지름 × RING_K × 0.79 (셰이더의 링 중심 r≈0.79) */
const RING_K = 2.4;
const TARGET_ALPHA = 0.9;

const TMP = new THREE.Vector3();
const HP = new THREE.Vector3();
const D = new THREE.Vector3();
const UPV = new THREE.Vector3();
const RGT = new THREE.Vector3();
const tmpObj = new THREE.Object3D();
const COLOR = new THREE.Color();

/**
 * 호버 노드의 이웃 행성 둘레에 뜨는 관계 유형색 링 (InstancedMesh SDF 1회).
 * - 색 = 역할의 관계 유형 색 (공급 하늘·투자 보라·협력 민트·경쟁 살구). 빔은 주황 그대로다
 * - 방향 관계는 링 위 쐐기로: 들어오는 관계(공급사·투자자)는 호버 노드를 향해, 나가는 관계(고객·피투자)는 이웃 안쪽을 향해
 * - 무방향(협력·경쟁)은 점선 링
 * - 빌보드라 카메라를 돌려도 정원이고, 쐐기 각도는 카메라 우·상 축에 투영한 호버 방향으로 프레임마다 갱신한다
 */
export default function RoleRings({ model, hoveredId, roles }: Props) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const live = liveFor(model);

  const built = useMemo(() => {
    const geo = new THREE.PlaneGeometry(2, 2);
    const dyn = (arr: Float32Array, itemSize: number) => {
      const a = new THREE.InstancedBufferAttribute(arr, itemSize);
      a.setUsage(THREE.DynamicDrawUsage);
      return a;
    };
    const color = dyn(new Float32Array(MAX * 3), 3);
    const dir = dyn(new Float32Array(MAX), 1);
    const angle = dyn(new Float32Array(MAX), 1);
    const alpha = dyn(new Float32Array(MAX), 1);
    geo.setAttribute("aColor", color);
    geo.setAttribute("aDir", dir);
    geo.setAttribute("aAngle", angle);
    geo.setAttribute("aAlpha", alpha);
    return { geo, color, dir, angle, alpha };
  }, []);
  useEffect(() => () => built.geo.dispose(), [built]);

  // 슬롯: 현재 링을 그리는 이웃 인덱스와 각 알파. roles 참조가 바뀌면 다시 채운다 (프레임 상태라 ref 에 둔다)
  const slotsRef = useRef({ model, roles: null as Map<string, RoleKey> | null, idx: [] as number[], alpha: new Float32Array(MAX) });

  useFrame((state, delta) => {
    const m = mesh.current;
    if (!m) return;
    const slots = slotsRef.current;
    // 모델이 바뀌면(산업 필터 등) 슬롯에 남은 인덱스는 이전 모델 기준이다. 그룹 key 가 같아 리마운트되지 않으므로 여기서 비운다 —
    // roles 가 null 인 채로 페이드 아웃 중이면 재충전 분기를 건너뛰어, 더 작아진 모델에서 model.nodes[ni] 가 undefined 가 된다
    if (slots.model !== model) {
      slots.model = model;
      slots.roles = null;
      slots.idx = [];
      slots.alpha.fill(0);
    }
    const dt = Math.min(delta, 0.05);
    // 0.15s 안에 거의 도달
    const k = 1 - Math.pow(0.0001, dt * 2);
    const cam = state.camera;
    const warping = useGalaxy.getState().phase === "warp";

    if (roles !== slots.roles) {
      slots.roles = roles;
      if (roles) {
        // 이웃 인덱스를 모으고(≤MAX, 큰 행성 우선) 색·방향을 쓴다. 알파는 0 에서 시작해 lerp 로 올라온다
        const list: number[] = [];
        roles.forEach((_, id) => {
          const node = model.nodeById.get(id);
          if (node) list.push(model.nodes.indexOf(node));
        });
        list.sort((a, b) => model.nodes[b].size - model.nodes[a].size);
        slots.idx = list.slice(0, MAX);
        slots.alpha.fill(0);
        const colArr = built.color.array as Float32Array;
        const dirArr = built.dir.array as Float32Array;
        slots.idx.forEach((ni, s) => {
          const role = roles.get(model.nodes[ni].id)!;
          COLOR.set(roleColor(role));
          colArr[s * 3] = COLOR.r;
          colArr[s * 3 + 1] = COLOR.g;
          colArr[s * 3 + 2] = COLOR.b;
          const d = ROLE_META[role].dir;
          dirArr[s] = d === "in" ? 1 : d === "out" ? 2 : 0;
        });
        touch(built.color, built.dir);
      }
      // roles 가 null 이면 슬롯은 유지한 채 알파만 0 으로 내려가고, 다 꺼지면 count 0
    }

    const hovered = hoveredId ? model.nodeById.get(hoveredId) : undefined;
    const hi = hovered ? model.nodes.indexOf(hovered) : -1;
    const target = roles && hi >= 0 && !warping && !morphState.plan ? TARGET_ALPHA : 0;
    if (hi >= 0) HP.fromArray(live.pos, hi * 3);
    UPV.set(0, 1, 0).applyQuaternion(cam.quaternion);
    RGT.set(1, 0, 0).applyQuaternion(cam.quaternion);

    const alphaArr = built.alpha.array as Float32Array;
    const angleArr = built.angle.array as Float32Array;
    let anyOn = false;
    slots.idx.forEach((ni, s) => {
      const a = (slots.alpha[s] += (target - slots.alpha[s]) * k);
      alphaArr[s] = a;
      if (a < 0.01) {
        tmpObj.position.set(0, 0, 0);
        tmpObj.scale.setScalar(0.001);
      } else {
        anyOn = true;
        const node = model.nodes[ni];
        TMP.fromArray(live.pos, ni * 3);
        const r = node.size * 1.3 * animFor(node.id).scale * live.scale[ni];
        tmpObj.position.copy(TMP);
        tmpObj.quaternion.copy(cam.quaternion);
        tmpObj.scale.setScalar(r * RING_K);
        // 빌보드 로컬 +x = 카메라 우, +y = 카메라 상. 호버 노드 방향을 그 축에 투영한 각도
        if (hi >= 0) {
          D.subVectors(HP, TMP);
          angleArr[s] = Math.atan2(D.dot(UPV), D.dot(RGT));
        }
      }
      tmpObj.updateMatrix();
      m.setMatrixAt(s, tmpObj.matrix);
    });
    m.count = anyOn ? slots.idx.length : 0;
    m.visible = anyOn;
    if (anyOn) {
      m.instanceMatrix.needsUpdate = true;
      touch(built.alpha, built.angle);
    }
  });

  return (
    <instancedMesh ref={mesh} args={[built.geo, undefined, MAX]} frustumCulled={false} renderOrder={4} raycast={() => null} visible={false}>
      <shaderMaterial vertexShader={RING_VERTEX} fragmentShader={RING_FRAGMENT} transparent depthTest={false} depthWrite={false} blending={THREE.NormalBlending} toneMapped={false} />
    </instancedMesh>
  );
}

const RING_VERTEX = /* glsl */ `
  attribute vec3 aColor;
  attribute float aDir;
  attribute float aAngle;
  attribute float aAlpha;
  varying vec2 vP;
  varying vec3 vColor;
  varying float vDir;
  varying float vAngle;
  varying float vAlpha;
  void main() {
    vP = uv * 2.0 - 1.0;
    vColor = aColor; vDir = aDir; vAngle = aAngle; vAlpha = aAlpha;
    gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0);
  }
`;
const RING_FRAGMENT = /* glsl */ `
  varying vec2 vP;
  varying vec3 vColor;
  varying float vDir;
  varying float vAngle;
  varying float vAlpha;
  void main() {
    if (vAlpha < 0.01) discard;
    float r = length(vP);
    float aa = max(fwidth(r), 1e-4) * 1.2;
    // 링 r ∈ [0.72, 0.86]
    float ring = smoothstep(0.72 - aa, 0.72 + aa, r) * (1.0 - smoothstep(0.86 - aa, 0.86 + aa, r));
    // 무방향은 점선 (12 칸)
    if (vDir < 0.5) ring *= step(0.5, fract(atan(vP.y, vP.x) / 6.2831853 * 12.0));
    // 쐐기: 호버 방향이 +x 가 되도록 돌린 좌표에서 x 축 위 삼각형.
    // in(1) 은 밑변 0.55 → 꼭짓점 1.0 (호버 노드를 가리킴), out(2) 은 밑변 1.0 → 꼭짓점 0.55 (이웃 안쪽)
    float wedge = 0.0;
    if (vDir > 0.5) {
      float c = cos(-vAngle);
      float s = sin(-vAngle);
      vec2 q = vec2(c * vP.x - s * vP.y, s * vP.x + c * vP.y);
      float bx = vDir < 1.5 ? 0.55 : 1.0;
      float tx = vDir < 1.5 ? 1.0 : 0.55;
      float u = (q.x - bx) / (tx - bx);
      float hw = 0.16 * (1.0 - u);
      wedge = step(0.0, u) * step(u, 1.0) * (1.0 - smoothstep(hw - aa, hw + aa, abs(q.y)));
    }
    float a = max(ring, wedge) * vAlpha;
    if (a < 0.004) discard;
    gl_FragColor = vec4(vColor, a);
  }
`;
