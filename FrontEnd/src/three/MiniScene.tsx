import { useMemo, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import * as THREE from "three";
import type { SceneModel } from "@/lib/graph";
import CompanyNodes from "./CompanyNodes";
import RelationLines from "./RelationLines";
import Backdrop from "./Backdrop";
import { LAB } from "./toon";
import { computeEmphasis } from "./emphasis";
import { dragState } from "./morph";

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

interface Props {
  model: SceneModel | null;
  onPickCompany?: (id: string) => void;
  onPickEdge?: (id: string) => void;
  highlightId?: string | null;
  autoRotate?: boolean;
}

/**
 * 뉴스·기업 페이지용 소형 관계 지도. 전역 은하 스토어와 분리된 로컬 상태로 동작한다.
 */
export default function MiniScene({ model, onPickCompany, onPickEdge, highlightId = null, autoRotate = true }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const emphasis = useMemo(
    () => (model ? computeEmphasis(model, { hoveredId: hovered ?? highlightId, hoveredEdgeId: hoveredEdge, selectedEdgeId: null, minScore: 0, activeTypes: new Set(["SUPPLY", "INVEST", "PARTNER", "COMPETE"]) }) : null),
    [model, hovered, hoveredEdge, highlightId],
  );
  const radius = useMemo(() => {
    if (!model) return 60;
    let r = 10;
    model.nodes.forEach((n) => {
      r = Math.max(r, Math.hypot(n.pos[0], n.pos[2]));
    });
    return r;
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
      <OrbitControls makeDefault enablePan={false} enableDamping dampingFactor={0.08} autoRotate={autoRotate && !hovered} autoRotateSpeed={0.6} minDistance={14} maxDistance={radius * 4 + 40} maxPolarAngle={Math.PI * 0.6} />
      <DragGuard />
      <Backdrop radius={600} stars={400} />
      <ambientLight intensity={0.55} color="#c9d4ff" />
      <hemisphereLight args={["#9ab3ff", "#3a1f5c", 0.6]} />
      <directionalLight position={[-40, 60, 50]} intensity={2.4} color="#fff3e0" />
      {model && emphasis && (
        <group key={model.centerId ?? "m"}>
          <RelationLines model={model} edges={model.edges} emphasis={emphasis} onHover={setHoveredEdge} onSelect={(id) => onPickEdge?.(id)} />
          <CompanyNodes model={model} emphasis={emphasis} hoveredId={hovered ?? highlightId} onHover={setHovered} onClick={(id) => onPickCompany?.(id)} />
        </group>
      )}
      <EffectComposer multisampling={0}>
        <Bloom mipmapBlur intensity={0.35} luminanceThreshold={0.88} luminanceSmoothing={0.2} radius={0.5} />
        <Vignette eskil={false} offset={0.2} darkness={0.7} />
      </EffectComposer>
    </Canvas>
  );
}
