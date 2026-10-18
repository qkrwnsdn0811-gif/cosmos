import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import * as THREE from "three";
import type { SceneModel } from "@/lib/graph";
import CompanyNodes from "./CompanyNodes";
import RelationLines from "./RelationLines";
import Backdrop from "./Backdrop";
import { LAB } from "./toon";
import { computeEmphasis, type Emphasis } from "./emphasis";
import { dragState } from "./morph";
import { fitDistance } from "./fit";

/**
 * 행성을 끄는 동안 궤도 회전을 멈춘다 — 메인 씬은 Director 가 같은 일을 하지만, 여기에는 연출 카메라가 없다.
 */
function DragGuard() {
  useFrame((state) => {
    const controls = state.controls as unknown as { enabled: boolean } | null;
    if (controls && controls.enabled === !!dragState.id) controls.enabled = !dragState.id;
  });
  return null;
}

/** 카메라가 원점을 내려다보는 방향 (위 0.9 : 앞 2.1 — 약 23°) */
const VIEW_DIR = new THREE.Vector3(0, 0.9, 2.1).normalize();

/**
 * 캔버스 비율에 맞춰 궤도 전체(반지름 radius, 높이 ±height)가 여유 있게 들어오도록 카메라 거리를 잡는다(fitDistance).
 * 좁은 세로 칸에서는 가로 시야가 좁아져 바깥 궤도 행성이 잘렸다 (이벤트 지도, 2026-10-19). 크기가 바뀌면 다시 맞춘다.
 */
function FitCamera({ radius, height }: { radius: number; height: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const size = useThree((s) => s.size);
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update(): void } | null;
  useEffect(() => {
    const dist = fitDistance(radius, height, camera.fov, size.width / Math.max(1, size.height));
    camera.position.copy(VIEW_DIR).multiplyScalar(dist);
    camera.lookAt(0, 0, 0);
    controls?.target.set(0, 0, 0);
    controls?.update();
  }, [camera, controls, radius, height, size.width, size.height]);
  return null;
}

interface Props {
  model: SceneModel | null;
  onPickCompany?: (id: string) => void;
  onPickEdge?: (id: string) => void;
  highlightId?: string | null;
  autoRotate?: boolean;
  /** 그릴 관계 종류 — 기본은 근거 관계 4종 */
  activeTypes?: Set<string>;
  /** 계산된 강조를 화면별로 고친다 (이벤트 지도: 보류 기업 흐리게, 이름표 항상, 가운데 기사 노드 숨김) */
  adjustEmphasis?: (e: Emphasis) => Emphasis;
  /** 이 값이 바뀌면 행성·선을 새로 만든다 — 중심 기업이 늘 같은 화면(이벤트 지도)은 이벤트 id 를 넘긴다 */
  sceneKey?: string;
  /** 씬 안에 더 그릴 것 (예: 이벤트 지도의 가운데 기사 별) */
  children?: ReactNode;
}

const RELATION_TYPES = new Set(["SUPPLY", "INVEST", "PARTNER", "COMPETE"]);

/**
 * 뉴스·기업 페이지용 소형 관계 지도. 전역 은하 스토어와 분리된 로컬 상태로 동작한다.
 */
export default function MiniScene({
  model,
  onPickCompany,
  onPickEdge,
  highlightId = null,
  autoRotate = true,
  activeTypes = RELATION_TYPES,
  adjustEmphasis,
  sceneKey,
  children,
}: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const emphasis = useMemo(() => {
    if (!model) return null;
    const e = computeEmphasis(model, { hoveredId: hovered ?? highlightId, hoveredEdgeId: hoveredEdge, selectedEdgeId: null, minScore: 0, activeTypes });
    return adjustEmphasis ? adjustEmphasis(e) : e;
  }, [model, hovered, hoveredEdge, highlightId, activeTypes, adjustEmphasis]);
  const { radius, height } = useMemo(() => {
    if (!model) return { radius: 60, height: 0 };
    let r = 10;
    let h = 0;
    model.nodes.forEach((n) => {
      r = Math.max(r, Math.hypot(n.pos[0], n.pos[2]));
      h = Math.max(h, Math.abs(n.pos[1]));
    });
    return { radius: r, height: h };
  }, [model]);

  return (
    <Canvas
      dpr={[1, 1.5]}
      gl={{ antialias: false, powerPreference: "high-performance" }}
      camera={{ fov: 48, near: 0.5, far: 2000, position: [0, radius * 0.9, radius * 2.1] }}
      raycaster={{ params: { Line: { threshold: 1.2 }, Points: { threshold: 1 }, Mesh: {}, LOD: {}, Sprite: {} } }}
      onCreated={({ gl }) => {
        gl.toneMapping = THREE.NoToneMapping;
        gl.setClearColor(LAB.bg, 1);
      }}
      style={{ cursor: hovered || hoveredEdge ? "pointer" : "grab" }}
    >
      <color attach="background" args={["#05070f"]} />
      <OrbitControls makeDefault enablePan={false} enableDamping dampingFactor={0.08} autoRotate={autoRotate && !hovered} autoRotateSpeed={0.6} minDistance={14} maxDistance={radius * 8 + 40} maxPolarAngle={Math.PI * 0.6} />
      <DragGuard />
      <FitCamera radius={radius} height={height} />
      <Backdrop radius={600} stars={400} />
      <ambientLight intensity={0.55} color="#c9d4ff" />
      <hemisphereLight args={["#9ab3ff", "#3a1f5c", 0.6]} />
      <directionalLight position={[-40, 60, 50]} intensity={2.4} color="#fff3e0" />
      {model && emphasis && (
        <group key={sceneKey ?? model.centerId ?? "m"}>
          <RelationLines model={model} edges={model.edges} emphasis={emphasis} onHover={setHoveredEdge} onSelect={(id) => onPickEdge?.(id)} />
          <CompanyNodes model={model} emphasis={emphasis} hoveredId={hovered ?? highlightId} onHover={setHovered} onClick={(id) => onPickCompany?.(id)} />
        </group>
      )}
      {children}
      <EffectComposer multisampling={0}>
        <Bloom mipmapBlur intensity={0.35} luminanceThreshold={0.88} luminanceSmoothing={0.2} radius={0.5} />
        <Vignette eskil={false} offset={0.2} darkness={0.7} />
      </EffectComposer>
    </Canvas>
  );
}
