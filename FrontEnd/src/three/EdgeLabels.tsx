import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import type { RelationshipType } from "@/api/types";
import type { SceneModel } from "@/lib/graph";
import { SURPRISE_UI } from "@/lib/guide";
import { RELATIONSHIP_META } from "@/lib/meta";
import { useGalaxy } from "@/store/galaxy";
import { altitudeGate } from "./altitude";
import { touch } from "./bufferUtil";
import { bezier, CHORD_CLEAR, controlPoint, radiusOf } from "./edgeCurve";
import type { Emphasis } from "./emphasis";
import { liveFor, morphState } from "./morph";
import { ensureNameCell, nameAtlasTexture, type NameCell } from "./nameAtlas";

interface Props {
  model: SceneModel;
  emphasis: Emphasis;
  /** 뜻밖 간선 id (lib/surprise 의 surpriseSet) — 앞에 SURPRISE_UI.mark 를 붙인다 */
  surprise: Set<string>;
}

/** 한 번에 그릴 라벨 상한 — 예산(점수 상위) 이후 겹침 제거로 실제 표시 수는 이보다 늘 적거나 같다 */
const MAX = 90;
/** 후보 재계산 주기(프레임) — 겹침 판정·투영이 간선 수만큼이라 매 프레임 돌리지 않는다. 위치 갱신(드래그 추종)은 매 프레임 그대로 한다 */
const FRAME_INTERVAL = 6;
/** 후보 최소 강조 — '보이는' 간선만 라벨 후보가 된다 (EdgeArrows.MIN_EMPH 과 같은 기준) */
const MIN_EMPH = 0.5;
/** 후보 최소 화면 길이(px) — 이보다 짧은 간선은 라벨을 놓을 자리가 없다 */
const MIN_SPAN_PX = 48;
/** 라벨 화면 높이(px) — 알약이 이 높이로 고정되도록 정점 셰이더가 거리에 따라 스케일을 잰다 */
const LABEL_PX = 18;
/** 겹침 판정 여백(px) — CompanyNodes 의 칩 겹침 판정과 같은 개념(CHIP_GAP) */
const GAP_PX = 4;
/** 알약 안에서 글자가 차지하는 가로 비율 밖의 여백 — 양옆에 이 비율만큼 비운다 (셰이더 PAD 와 값을 맞춘다) */
const PAD = 0.12;

const CTRL = new THREE.Vector3();
const MID = new THREE.Vector3();
const UPV = new THREE.Vector3();
const PA = new THREE.Vector3();
const PB = new THREE.Vector3();
const PL = new THREE.Vector3();
const MAT = new THREE.Matrix4();

/** uniform.value 대입을 감싼다 — useMemo 로 만든 uniforms 를 useFrame 안에서 직접 대입하면 react-hooks/immutability 에 걸린다 (touch() 와 같은 이유) */
function setUniform(u: { value: number }, v: number) {
  u.value = v;
}

/** 관계 종류 라벨 문자열 — 유형(4) × 뜻밖 여부(2) = 최대 8종뿐이라 아틀라스 굽기 비용이 없다 (ensureNameCell 이 텍스트별로 캐시한다) */
function labelText(type: RelationshipType, isSurprise: boolean) {
  const meta = RELATIONSHIP_META[type];
  const base = `${meta?.label ?? String(type)} ${meta?.directed ? "▸" : "⇄"}`;
  return isSurprise ? `${SURPRISE_UI.mark} ${base}` : base;
}

interface Rect {
  edgeId: string;
  score: number;
  cell: NameCell;
  aspect: number;
  /** 겹침 판정용 화면 사각형 (여백 포함): left, top, right, bottom */
  rect: [number, number, number, number];
}
interface Selected {
  edgeId: string;
  cell: NameCell;
  aspect: number;
}

/** 워프·모프·주가 고도 전이 중에는 간선 자체가 숨어 있다 — 그 위의 라벨도 같은 규칙으로 숨긴다 (EdgeArrows 와 동일 기준) */
function labelsOpen() {
  const s = useGalaxy.getState();
  if (!s.showEdgeLabels || s.phase === "warp" || morphState.plan) return false;
  if (morphState.reveal < 0.99) return false;
  const alt = altitudeGate();
  return alt.fade >= 0.99 && alt.reveal >= 0.99;
}

/**
 * 간선 관계 종류 라벨 — 선 중간에 "공급 ▸" 처럼 뜬다 (기획서 P0-5).
 * 글자는 기업명 칩과 같은 이름 아틀라스(nameAtlas)를 구워 InstancedMesh 1개 + 셰이더로 그린다.
 * 색은 유형색이 아니라 무채색 — "색 = 영향 방향" 원칙을 지키기 위해 라벨은 항상 같은 회백색이다.
 */
export default function EdgeLabels({ model, emphasis, surprise }: Props) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const clear = CHORD_CLEAR[model.kind];
  const live = liveFor(model);
  const nodeIndex = useMemo(() => {
    const map = new Map<string, number>();
    model.nodes.forEach((nd, i) => map.set(nd.id, i));
    return map;
  }, [model]);

  const built = useMemo(() => {
    const geo = new THREE.PlaneGeometry(1, 1);
    const cell = new THREE.InstancedBufferAttribute(new Float32Array(MAX * 4), 4);
    const aspect = new THREE.InstancedBufferAttribute(new Float32Array(MAX), 1);
    const alpha = new THREE.InstancedBufferAttribute(new Float32Array(MAX), 1);
    cell.setUsage(THREE.DynamicDrawUsage);
    aspect.setUsage(THREE.DynamicDrawUsage);
    alpha.setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute("aCell", cell);
    geo.setAttribute("aAspect", aspect);
    geo.setAttribute("aAlpha", alpha);
    return { geo, cell, aspect, alpha };
  }, []);
  useEffect(() => () => built.geo.dispose(), [built]);

  const uniforms = useMemo(() => ({ uAtlas: { value: nameAtlasTexture() }, uWorldPerPx: { value: 0 } }), []);

  const frame = useRef(0);
  const ready = useRef(false);
  const selected = useRef<Selected[]>([]);

  /** 6프레임마다 후보를 모아 점수 상위 MAX 를 뽑고, 화면 사각형이 겹치면 그리디로 낮은 점수를 버린다. 위치·알파는 매 프레임 갱신한다 */
  useFrame((state) => {
    const m = mesh.current;
    if (!m) return;
    frame.current += 1;
    const open = labelsOpen();
    if (!open) {
      m.count = 0;
      return;
    }

    const camera = state.camera as THREE.PerspectiveCamera;
    const fov = camera.isPerspectiveCamera ? camera.fov : 52;
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(fov) / 2);
    const W = state.size.width;
    const H = state.size.height;
    // R3F 는 <shaderMaterial uniforms={…}> 를 재질에 깊은 복사한다 — useMemo 객체를 고쳐도 GPU 에 가지 않아 쿼드가 0 크기로 남는다(실측).
    // 그래서 메시에 실제로 붙은 재질의 uniforms 를 고친다
    setUniform((m.material as THREE.ShaderMaterial).uniforms.uWorldPerPx as { value: number }, (2 * tanHalf) / Math.max(1, H));
    UPV.set(0, 1, 0).applyQuaternion(camera.quaternion);

    const doRecompute = !ready.current || frame.current % FRAME_INTERVAL === 0;
    if (doRecompute) {
      ready.current = true;
      const candidates: Rect[] = [];
      model.edges.forEach((e) => {
        const emph = emphasis.edge.get(e.id) ?? 1;
        if (emph < MIN_EMPH) return;
        const si = nodeIndex.get(e.source);
        const ti = nodeIndex.get(e.target);
        if (si === undefined || ti === undefined) return;

        PA.fromArray(live.pos, si * 3).project(camera);
        PB.fromArray(live.pos, ti * 3).project(camera);
        const ax = ((PA.x + 1) / 2) * W;
        const ay = ((1 - PA.y) / 2) * H;
        const bx = ((PB.x + 1) / 2) * W;
        const by = ((1 - PB.y) / 2) * H;
        if (Math.hypot(bx - ax, by - ay) < MIN_SPAN_PX) return;

        controlPoint(live.pos, live.pos, CTRL, si * 3, ti * 3, clear);
        bezier(live.pos, live.pos, CTRL, 0.5, MID, si * 3, ti * 3);
        MID.addScaledVector(UPV, radiusOf(e.score / 100) * 3 + 0.6);
        PL.copy(MID).project(camera);
        if (PL.z >= 1) return; // 카메라 뒤

        const isSurprise = surprise.has(e.id);
        const cell = ensureNameCell(labelText(e.type, isSurprise));
        if (!cell) return;
        const aspect = cell.aspect / (1 - 2 * PAD);

        const lx = ((PL.x + 1) / 2) * W;
        const ly = ((1 - PL.y) / 2) * H;
        const halfW = (LABEL_PX * aspect) / 2 + GAP_PX;
        const halfH = LABEL_PX / 2 + GAP_PX;
        candidates.push({ edgeId: e.id, score: e.score, cell, aspect, rect: [lx - halfW, ly - halfH, lx + halfW, ly + halfH] });
      });

      candidates.sort((a, b) => b.score - a.score);
      const top = candidates.slice(0, MAX);
      const kept: Rect[] = [];
      top.forEach((c) => {
        const hit = kept.some((k) => c.rect[0] < k.rect[2] && k.rect[0] < c.rect[2] && c.rect[1] < k.rect[3] && k.rect[1] < c.rect[3]);
        if (!hit) kept.push(c);
      });
      selected.current = kept.map((c) => ({ edgeId: c.edgeId, cell: c.cell, aspect: c.aspect }));
    }

    const cellArr = built.cell.array as Float32Array;
    const aspectArr = built.aspect.array as Float32Array;
    const alphaArr = built.alpha.array as Float32Array;
    let count = 0;
    selected.current.forEach((s) => {
      const edge = model.edgeById.get(s.edgeId);
      if (!edge) return;
      const si = nodeIndex.get(edge.source);
      const ti = nodeIndex.get(edge.target);
      if (si === undefined || ti === undefined) return;

      controlPoint(live.pos, live.pos, CTRL, si * 3, ti * 3, clear);
      bezier(live.pos, live.pos, CTRL, 0.5, MID, si * 3, ti * 3);
      MID.addScaledVector(UPV, radiusOf(edge.score / 100) * 3 + 0.6);
      MAT.makeTranslation(MID.x, MID.y, MID.z);
      m.setMatrixAt(count, MAT);

      const o = count * 4;
      cellArr[o] = s.cell.u;
      cellArr[o + 1] = s.cell.v;
      cellArr[o + 2] = s.cell.w;
      cellArr[o + 3] = s.cell.h;
      aspectArr[count] = s.aspect;
      alphaArr[count] = Math.min(1, emphasis.edge.get(s.edgeId) ?? 1);
      count += 1;
    });
    m.count = count;
    m.instanceMatrix.needsUpdate = true;
    touch(built.cell, built.aspect, built.alpha);
  });

  return (
    <instancedMesh ref={mesh} args={[built.geo, undefined, MAX]} frustumCulled={false} renderOrder={4} raycast={() => null}>
      {/* depthTest 를 끈다 — 글자는 씬의 물체가 아니라 주석이다. 핵처럼 행성이 촘촘한 곳의 짧은 빔은 중점이 행성 안에 묻혀 깊이 검사에 걸리면 라벨이 통째로 사라진다 */}
      <shaderMaterial uniforms={uniforms} vertexShader={LABEL_VERTEX} fragmentShader={LABEL_FRAGMENT} transparent depthWrite={false} depthTest={false} toneMapped={false} />
    </instancedMesh>
  );
}

const LABEL_VERTEX = /* glsl */ `
  attribute vec4 aCell;
  attribute float aAspect;
  attribute float aAlpha;
  uniform float uWorldPerPx;
  varying vec2 vUv;
  varying vec4 vCell;
  varying float vAspect;
  varying float vAlpha;
  void main() {
    vUv = uv;
    vCell = aCell;
    vAspect = aAspect;
    vAlpha = aAlpha;
    // 인스턴스 중심을 뷰 공간에서 구하고, 로컬 쿼드 오프셋은 투영 전에 뷰 공간 XY 에 직접 더한다 — 카메라를 그대로 따라가는 빌보드가 된다
    vec4 mv = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
    float worldH = ${LABEL_PX.toFixed(1)} * uWorldPerPx * max(0.001, -mv.z);
    float worldW = worldH * aAspect;
    mv.xy += vec2(position.x * worldW, position.y * worldH);
    gl_Position = projectionMatrix * mv;
  }
`;
const LABEL_FRAGMENT = /* glsl */ `
  uniform sampler2D uAtlas;
  varying vec2 vUv;
  varying vec4 vCell;
  varying float vAspect;
  varying float vAlpha;
  void main() {
    if (vAlpha <= 0.003) discard;
    // 둥근 알약 마스크 — 세로 1, 가로 aspect 인 캡슐 (반지름 0.5)
    vec2 q = vec2((vUv.x - 0.5) * vAspect, vUv.y - 0.5);
    float halfW = max(0.0, vAspect * 0.5 - 0.5);
    float d = length(vec2(q.x - clamp(q.x, -halfW, halfW), q.y)) - 0.5;
    float aa = 0.035;
    float mask = 1.0 - smoothstep(-aa, aa, d);
    if (mask <= 0.004) discard;
    vec3 col = vec3(0.043, 0.063, 0.125); // rgba(11,16,32,.62) 의 rgb
    float alpha = 0.62 * mask;
    // 글자 — 가로 PAD 만큼 안쪽으로 넣고, 아틀라스는 캔버스 좌표(y 아래)라 v 를 뒤집어 읽는다.
    // r*a 로 그림자 헤일로를 걸러내(짙은 그림자는 r 이 거의 0) 순수 흰 글자만 마스크로 쓰고, 색은 무채색(#E6ECF5)으로 다시 칠한다 — 유형색을 쓰지 않는다
    float nu = (vUv.x - ${PAD.toFixed(2)}) / ${(1 - 2 * PAD).toFixed(2)};
    if (nu >= 0.0 && nu <= 1.0) {
      vec4 tx = texture2D(uAtlas, vec2(vCell.x + nu * vCell.z, vCell.y + (1.0 - vUv.y) * vCell.w));
      float glyph = tx.r * tx.a;
      col = mix(col, vec3(0.902, 0.925, 0.961), glyph);
      alpha = max(alpha, glyph);
    }
    gl_FragColor = vec4(col, alpha * vAlpha);
  }
`;
