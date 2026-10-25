import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { ATLAS_CELL_UV, logoAtlasTexture, nodeLogoCell } from "./logoAtlas";
import { discFragment, discVertex } from "./logoDiscShader";
import { morphState, type MorphPlan } from "./morph";

/** 고스트 상한 — 은하 188개를 다 담고도 남는다 */
const MAX = 256;
/** 이탈 수명(초) — 커밋 직후 이 시간 안에 축소·소멸한다 */
const LIFE = 0.45;
/** 자기 방위 바깥으로 튕겨 나가는 거리(월드) */
const FLING = 80;

const easeInQuad = (x: number) => x * x;
const tmpObj = new THREE.Object3D();
const COLOR = new THREE.Color();
const POS = new THREE.Vector3();
const TO_CAM = new THREE.Vector3();

/**
 * 워프 모프에서 사라지는 행성 — 출발지(은하)에만 있고 목적지(기업 중심 관계망)에는 없는 기업.
 * 팝으로 사라지는 대신 자기 방위 바깥으로 튕겨 나가며 0.45s 안에 줄어들어 없어진다.
 * 행성(CompanyNodes)과 같은 로고 원판(같은 셰이더·같은 아틀라스 셀)이라, 날아가는 동안에도 어느 기업인지 그대로 보인다.
 * keyed group 밖에 상시 마운트되어 모델 교체(리마운트)와 무관하게 계획이 끝날 때까지 살아 있고,
 * plan 이 없으면 count 0 이라 드로우콜도 0 이다.
 */
export default function GhostNodes() {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const built = useMemo(() => {
    const geo = new THREE.PlaneGeometry(2, 2);
    const color = new THREE.InstancedBufferAttribute(new Float32Array(MAX * 3), 3);
    const cell = new THREE.InstancedBufferAttribute(new Float32Array(MAX * 2), 2);
    geo.setAttribute("aColor", color);
    geo.setAttribute("aCell", cell);
    geo.setAttribute("aBright", new THREE.InstancedBufferAttribute(new Float32Array(MAX).fill(1), 1));
    geo.setAttribute("aEmph", new THREE.InstancedBufferAttribute(new Float32Array(MAX).fill(1), 1));
    geo.setAttribute("aAlpha", new THREE.InstancedBufferAttribute(new Float32Array(MAX).fill(1), 1));
    return { geo, color, cell };
  }, []);
  useEffect(() => () => built.geo.dispose(), [built]);
  const uniforms = useMemo(() => ({ uAtlas: { value: logoAtlasTexture() }, uCell: { value: ATLAS_CELL_UV } }), []);
  /** 색·셀·개수를 다시 쓸 계획인지 판별하는 표식 (계획 객체 참조가 바뀌면 새 계획) */
  const seen = useRef<MorphPlan | null>(null);

  useEffect(() => {
    const m = mesh.current;
    if (!m) return;
    m.count = 0;
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  }, []);

  /* eslint-disable react-hooks/immutability -- R3F updates GPU buffers imperatively in its frame callback. */
  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    const plan = morphState.plan;
    if (!plan || plan.ghosts.length === 0) {
      seen.current = null;
      m.count = 0;
      m.visible = false;
      return;
    }
    const count = Math.min(MAX, plan.ghosts.length);
    if (seen.current !== plan) {
      seen.current = plan;
      const colors = built.color.array as Float32Array;
      const cells = built.cell.array as Float32Array;
      for (let i = 0; i < count; i += 1) {
        const g = plan.ghosts[i];
        COLOR.set(g.color);
        colors[i * 3] = COLOR.r;
        colors[i * 3 + 1] = COLOR.g;
        colors[i * 3 + 2] = COLOR.b;
        const [u, v] = nodeLogoCell(g.node);
        cells[i * 2] = u;
        cells[i * 2 + 1] = v;
      }
      built.color.needsUpdate = true;
      built.cell.needsUpdate = true;
    }
    // plan.t 는 커밋~착지 구간의 진행도라 실제 초로 되돌려 수명과 견준다
    const u = Math.min(1, (plan.t * plan.dur) / LIFE);
    if (u >= 1) {
      m.count = 0;
      m.visible = false;
      return;
    }
    const cam = state.camera;
    const push = easeInQuad(u) * FLING;
    const shrink = Math.pow(1 - u, 1.5);
    for (let i = 0; i < count; i += 1) {
      const g = plan.ghosts[i];
      const r = Math.max(1e-3, g.radius * shrink);
      POS.set(g.pos[0] + g.dir[0] * push, g.pos[1] + g.dir[1] * push, g.pos[2] + g.dir[2] * push);
      // 행성 원판과 같이 중심에서 카메라 쪽으로 반지름만큼 나온 자리에 빌보드로 놓는다
      TO_CAM.copy(cam.position).sub(POS).normalize();
      tmpObj.position.copy(POS).addScaledVector(TO_CAM, r);
      tmpObj.quaternion.copy(cam.quaternion);
      tmpObj.scale.setScalar(r);
      tmpObj.updateMatrix();
      m.setMatrixAt(i, tmpObj.matrix);
    }
    m.count = count;
    m.visible = true;
    m.instanceMatrix.needsUpdate = true;
  });
  /* eslint-enable react-hooks/immutability */

  return (
    <instancedMesh ref={mesh} args={[built.geo, undefined, MAX]} frustumCulled={false} raycast={() => null} visible={false} renderOrder={-1}>
      <shaderMaterial vertexShader={discVertex} fragmentShader={discFragment} uniforms={uniforms} transparent depthWrite toneMapped={false} />
    </instancedMesh>
  );
}
