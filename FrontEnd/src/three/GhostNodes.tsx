import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { morphState, type MorphPlan } from "./morph";
import { toonGradient } from "./toon";

/** 고스트 상한 — 은하 188개를 다 담고도 남는다 */
const MAX = 256;
/** 이탈 수명(초) — 커밋 직후 이 시간 안에 축소·소멸한다 */
const LIFE = 0.45;
/** 자기 방위 바깥으로 튕겨 나가는 거리(월드) */
const FLING = 80;
/** 항성 점광 레이어 — 행성과 같은 조명을 받도록 */
const LIT = 1;

const easeInQuad = (x: number) => x * x;
const tmpObj = new THREE.Object3D();
const COLOR = new THREE.Color();

/**
 * 워프 모프에서 사라지는 행성 — 출발지(은하)에만 있고 목적지(기업 중심 관계망)에는 없는 기업.
 * 팝으로 사라지는 대신 자기 방위 바깥으로 튕겨 나가며 0.45s 안에 줄어들어 없어진다.
 * keyed group 밖에 상시 마운트되어 모델 교체(리마운트)와 무관하게 계획이 끝날 때까지 살아 있고,
 * plan 이 없으면 count 0 이라 드로우콜도 0 이다.
 */
export default function GhostNodes() {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const geo = useMemo(() => {
    // 행성과 같은 저폴리 툰 실루엣 — 면 단위 음영이 나오도록 인덱스를 풀고 법선을 다시 만든다
    const g = new THREE.IcosahedronGeometry(1, 1).toNonIndexed();
    g.computeVertexNormals();
    return g;
  }, []);
  useEffect(() => () => geo.dispose(), [geo]);
  const grad = toonGradient(3);
  /** 색·개수를 다시 쓸 계획인지 판별하는 표식 (계획 객체 참조가 바뀌면 새 계획) */
  const seen = useRef<MorphPlan | null>(null);

  useEffect(() => {
    const m = mesh.current;
    if (!m) return;
    m.count = 0;
    m.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  }, []);

  useFrame(() => {
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
      for (let i = 0; i < count; i += 1) {
        COLOR.set(plan.ghosts[i].color);
        m.setColorAt(i, COLOR);
      }
      if (m.instanceColor) m.instanceColor.needsUpdate = true;
    }
    // plan.t 는 커밋~착지 구간의 진행도라 실제 초로 되돌려 수명과 견준다
    const u = Math.min(1, (plan.t * plan.dur) / LIFE);
    if (u >= 1) {
      m.count = 0;
      m.visible = false;
      return;
    }
    const push = easeInQuad(u) * FLING;
    const shrink = Math.pow(1 - u, 1.5);
    for (let i = 0; i < count; i += 1) {
      const g = plan.ghosts[i];
      tmpObj.position.set(g.pos[0] + g.dir[0] * push, g.pos[1] + g.dir[1] * push, g.pos[2] + g.dir[2] * push);
      tmpObj.scale.setScalar(Math.max(1e-3, g.radius * shrink));
      tmpObj.updateMatrix();
      m.setMatrixAt(i, tmpObj.matrix);
    }
    m.count = count;
    m.visible = true;
    m.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh
      ref={mesh}
      args={[geo, undefined, MAX]}
      frustumCulled={false}
      raycast={() => null}
      visible={false}
      renderOrder={2}
      onUpdate={(m) => m.layers.enable(LIT)}
    >
      {/* 인스턴스 색(setColorAt)이 곱해지므로 재질은 흰색 */}
      <meshToonMaterial color="#ffffff" gradientMap={grad} />
    </instancedMesh>
  );
}
