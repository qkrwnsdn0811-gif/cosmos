import { useCallback, useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { SceneModel } from "@/lib/graph";
import { useGalaxy } from "@/store/galaxy";
import { altitudeGate } from "./altitude";
import { bezier, CHORD_CLEAR, controlPoint, radiusOf } from "./edgeCurve";
import type { Emphasis } from "./emphasis";
import { dragState, liveFor, morphState } from "./morph";
import { beamColorFor } from "./toon";

interface Props {
  model: SceneModel;
  emphasis: Emphasis;
}

/** 화살촉 상한 — 은하 예산(상위 8%, ≈40)·기업 중심 뷰 depth-1 전체에 충분하고, 예산 off 상태에서는 점수 상위만 */
const MAX = 160;
/**
 * 화살촉이 붙는 최소 강조 — '밝은' 간선이 아니라 '보이는' 간선 기준.
 * 기업 중심 뷰의 depth 2·3 간선은 기본 강조가 0.74·0.56(DEPTH_DIM)이라 0.9 로 자르면 바깥 궤도에 화살촉이 없어진다.
 * 흐려 둔 상태(0.05·0.08·0.21)는 이 아래라 그대로 빠진다.
 */
const MIN_EMPH = 0.5;
/** 콘 크기(월드) — 튜브 반지름의 배수, 멀리서도 보이도록 하한을 둔다 */
const CONE_K = 2.6;
const CONE_MIN = 0.36;
/** 콘 지오메트리의 높이 (반지름 1 기준) */
const CONE_H = 2.2;

const Y = new THREE.Vector3(0, 1, 0);
const CTRL = new THREE.Vector3();
const P = new THREE.Vector3();
const Q = new THREE.Vector3();
const DIR = new THREE.Vector3();
const tmpObj = new THREE.Object3D();
const COLOR = new THREE.Color();

/**
 * 방향 간선의 target 끝 화살촉 — 빔과 같은 색의 콘 (InstancedMesh 1회).
 * 펄스 방향은 눈으로 따라가야 읽히지만 화살촉은 정지 화면에서도 방향을 확정한다.
 * 색은 빔 규칙을 그대로 따른다(영향 방향별 파랑·빨강·보라) — 관계 유형 색은 노드 쪽 요소에만 쓴다.
 * 위치는 target 행성 표면 바로 바깥 — 짧은 간선에서 고정 t(0.93) 를 쓰면 콘이 행성 안에 묻히므로,
 * 끝점 거리에서 행성 반지름 + 콘 반 높이만큼 물러난 t 를 쓴다.
 * 끝점은 node.pos 가 아니라 live 버퍼에서 읽는다 — 드래그로 옮긴 행성에도 화살촉이 따라붙는다.
 */
export default function EdgeArrows({ model, emphasis }: Props) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const geo = useMemo(() => new THREE.ConeGeometry(1, CONE_H, 8), []);
  useEffect(() => () => geo.dispose(), [geo]);

  const live = liveFor(model);
  const nodeIndex = useMemo(() => {
    const m = new Map<string, number>();
    model.nodes.forEach((nd, i) => m.set(nd.id, i));
    return m;
  }, [model]);
  const targets = useMemo(
    () => model.edges.filter((e) => e.directed && (emphasis.edge.get(e.id) ?? 1) >= MIN_EMPH).sort((a, b) => b.score - a.score).slice(0, MAX),
    [model, emphasis],
  );

  /** 화살촉 배치 — 강조·모델이 바뀔 때와 드래그로 행성이 움직인 프레임에 부른다 */
  const place = useCallback(() => {
    const m = mesh.current;
    if (!m) return;
    let count = 0;
    targets.forEach((e) => {
      const si = nodeIndex.get(e.source);
      const ti = nodeIndex.get(e.target);
      if (si === undefined || ti === undefined) return;
      const i = count;
      count += 1;
      const emph = emphasis.edge.get(e.id) ?? 1;
      const s = e.score / 100;
      const scale = Math.max(CONE_MIN, radiusOf(s) * CONE_K);
      // 빔(RelationLines)과 같은 clear 를 줘야 화살촉이 빔 위에 놓인다
      const dist = controlPoint(live.pos, live.pos, CTRL, si * 3, ti * 3, CHORD_CLEAR[model.kind]);
      // target 행성 반지름(size×1.3)의 바깥 + 콘 반 높이만큼 끝에서 물러난다 (곡선 끝부분은 거의 직선이라 거리 비로 근사)
      const rT = (model.nodeById.get(e.target)?.size ?? 1) * 1.3;
      const back = rT * 1.15 + (scale * CONE_H) / 2;
      const t = THREE.MathUtils.clamp(1 - back / Math.max(1e-3, dist), 0.5, 0.93);
      bezier(live.pos, live.pos, CTRL, t, P, si * 3, ti * 3);
      bezier(live.pos, live.pos, CTRL, Math.min(1, t + 0.03), Q, si * 3, ti * 3);
      bezier(live.pos, live.pos, CTRL, Math.max(0, t - 0.03), DIR, si * 3, ti * 3);
      DIR.subVectors(Q, DIR);
      if (DIR.lengthSq() < 1e-8) DIR.copy(Y);
      DIR.normalize();
      tmpObj.position.copy(P);
      tmpObj.quaternion.setFromUnitVectors(Y, DIR);
      tmpObj.scale.setScalar(scale);
      tmpObj.updateMatrix();
      m.setMatrixAt(i, tmpObj.matrix);
      // 빔과 달리 여기는 meshBasicMaterial 이라 three 가 출력 색공간 변환을 붙여 준다 —
      // 그래서 THREE.Color 의 기본 sRGB→작업공간 변환을 그대로 태워야 화면에서 빔과 같은 색이 된다
      COLOR.set(beamColorFor(e.impact));
      // 빔은 강조로 투명해지는데 화살촉은 불투명 재질이라, 어두운 배경 위에서 같은 정도로 물러나도록 색을 눌러 준다
      COLOR.multiplyScalar(0.35 + 0.65 * emph);
      m.setColorAt(i, COLOR);
    });
    m.count = count;
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [targets, model, emphasis, live, nodeIndex]);
  useEffect(() => place(), [place]);

  /** 드래그 추종에서 마지막으로 반영한 버전, 그리고 지금 숨어 있는지(= 다시 보일 때 한 번 다시 놓아야 하는지) */
  const followed = useRef({ version: dragState.version, gated: true }).current;

  // 모프·워프 중에는 간선이 사라지므로 화살촉도 숨긴다 (프레임마다 getState 로 읽어 리렌더를 만들지 않는다)
  useFrame(() => {
    const m = mesh.current;
    if (!m) return;
    if (followed.version !== dragState.version) {
      followed.version = dragState.version;
      place();
    }
    const warping = useGalaxy.getState().phase === "warp";
    // 주가 고도 전이 중에는 빔이 꺼져 있고 끝점도 이동 끝에 한 번만 갱신된다 — 화살촉만 옛 자리에 남지 않게 함께 숨긴다
    const alt = altitudeGate();
    const open = !warping && !morphState.plan && morphState.reveal >= 0.99 && alt.fade >= 0.99 && alt.reveal >= 0.99;
    // 숨어 있는 동안 live 는 중간 좌표였다 — 마운트 시점(워프 커밋)에 놓은 자리는 이전 배치의 것이므로,
    // 다시 보이기 직전에 한 번 더 놓는다. 열려 있는 동안은 드래그 버전만 보면 된다
    if (open && followed.gated) place();
    followed.gated = !open;
    m.visible = open && m.count > 0;
  });

  return (
    <instancedMesh ref={mesh} args={[geo, undefined, MAX]} frustumCulled={false} renderOrder={3} raycast={() => null}>
      {/* 색은 인스턴스 색(setColorAt)이 곱해지므로 재질은 흰색.
          안개는 끈다 — 같은 간선의 튜브·헤일로·구슬 ShaderMaterial 이 안개를 받지 않아, 켜 두면 먼 성단에서 화살촉만 사라진다 */}
      <meshBasicMaterial color="#ffffff" toneMapped={false} fog={false} />
    </instancedMesh>
  );
}
