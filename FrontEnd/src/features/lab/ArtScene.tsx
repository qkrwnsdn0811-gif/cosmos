import { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import * as THREE from "three";
import Backdrop from "@/three/Backdrop";
import { LAB, glowTexture, logoTagTexture, planetGeometry, toonGradient, type PlanetPalette } from "./toon";

export interface LabOptions {
  steps: number;
  glow: boolean;
  spin: boolean;
}

/* ------------------------------ 항성 ------------------------------ */
/** 항성 점광은 레이어 1 에만 닿는다 — 행성은 레이어 1 을 켜서 빛을 받고, 항성 자신은 받지 않아 셀 음영이 남는다 */
const LIT = 1;
function Star({ position, radius, steps, glow }: { position: [number, number, number]; radius: number; steps: number; glow: boolean }) {
  const ref = useRef<THREE.Group>(null);
  const light = useRef<THREE.PointLight>(null);
  useEffect(() => {
    light.current?.layers.set(LIT);
  }, []);
  // 인덱스를 풀어 면마다 평평한 법선을 갖게 한다 (툰 머티리얼은 flatShading 옵션이 없다)
  const geo = useMemo(() => {
    const base = new THREE.IcosahedronGeometry(radius, 2);
    const g = base.index ? base.toNonIndexed() : base;
    g.computeVertexNormals();
    return g;
  }, [radius]);
  useEffect(() => () => geo.dispose(), [geo]);
  useFrame(({ clock }) => {
    const g = ref.current;
    if (!g) return;
    const t = clock.elapsedTime;
    g.rotation.y = t * 0.12;
    g.scale.setScalar(1 + Math.sin(t * 1.6) * 0.015);
  });
  return (
    <group position={position}>
      <group ref={ref}>
        <mesh geometry={geo}>
          <meshToonMaterial color={LAB.star} emissive={LAB.starHot} emissiveIntensity={0.1} gradientMap={toonGradient(steps)} />
        </mesh>
      </group>
      {glow && (
        <>
          <sprite scale={[radius * 5.2, radius * 5.2, 1]}>
            <spriteMaterial map={glowTexture()} color={LAB.starHot} transparent opacity={0.18} depthWrite={false} blending={THREE.AdditiveBlending} />
          </sprite>
          <sprite scale={[radius * 2.9, radius * 2.9, 1]}>
            <spriteMaterial map={glowTexture()} color={LAB.star} transparent opacity={0.3} depthWrite={false} blending={THREE.AdditiveBlending} />
          </sprite>
        </>
      )}
      <pointLight ref={light} color={LAB.star} intensity={260} distance={70} decay={2} />
    </group>
  );
}

/* ------------------------------ 행성 ------------------------------ */
interface PlanetProps {
  position: [number, number, number];
  radius: number;
  palette: PlanetPalette;
  seed: number;
  variant: "land" | "ring" | "moon";
  steps: number;
  spin: boolean;
  tag?: { url: string | null; monogram: string };
}
function Planet({ position, radius, palette, seed, variant, steps, spin, tag }: PlanetProps) {
  const body = useRef<THREE.Mesh>(null);
  const moon = useRef<THREE.Group>(null);
  const geo = useMemo(() => planetGeometry(radius, palette, seed, 2, variant === "ring" ? 0.3 : 0.45), [radius, palette, seed, variant]);
  const moonGeo = useMemo(() => planetGeometry(radius * 0.28, palette, seed + 5, 1, 0.2), [radius, palette, seed]);
  const tagTex = useMemo(() => (tag ? logoTagTexture(tag.url, tag.monogram, palette.ocean) : null), [tag, palette]);
  useEffect(
    () => () => {
      geo.dispose();
      moonGeo.dispose();
    },
    [geo, moonGeo],
  );
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    if (body.current && spin) body.current.rotation.y = t * 0.15 + seed;
    if (moon.current) moon.current.rotation.y = t * 0.55 + seed;
  });
  const grad = toonGradient(steps);
  return (
    <group position={position}>
      <mesh ref={body} geometry={geo} onUpdate={(m) => m.layers.enable(LIT)}>
        <meshToonMaterial vertexColors gradientMap={grad} />
      </mesh>
      {variant === "ring" && (
        <mesh rotation={[Math.PI / 2 - 0.42, 0.25, 0]} onUpdate={(m) => m.layers.enable(LIT)}>
          <ringGeometry args={[radius * 1.45, radius * 2.1, 48, 1]} />
          <meshToonMaterial color={palette.ring} gradientMap={grad} side={THREE.DoubleSide} transparent opacity={0.92} />
        </mesh>
      )}
      {variant === "moon" && (
        <group ref={moon} rotation={[0.3, 0, 0.15]}>
          <mesh geometry={moonGeo} position={[radius * 2.1, 0, 0]} onUpdate={(m) => m.layers.enable(LIT)}>
            <meshToonMaterial vertexColors gradientMap={grad} />
          </mesh>
        </group>
      )}
      {tagTex && (
        <sprite position={[radius * 0.95, radius * 1.15, radius * 0.6]} scale={[radius * 0.9, radius * 0.9, 1]}>
          <spriteMaterial map={tagTex} transparent depthWrite={false} />
        </sprite>
      )}
    </group>
  );
}

/* ------------------------------ 관계 빔 ------------------------------ */
function Beam({ from, to, glow, bow = 0.25 }: { from: [number, number, number]; to: [number, number, number]; glow: boolean; bow?: number }) {
  const { curve, tube, halo } = useMemo(() => {
    const a = new THREE.Vector3(...from);
    const b = new THREE.Vector3(...to);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    const d = a.distanceTo(b);
    mid.y += d * bow;
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
    return { curve, tube: new THREE.TubeGeometry(curve, 48, 0.09, 8, false), halo: new THREE.TubeGeometry(curve, 48, 0.3, 8, false) };
  }, [from, to, bow]);
  useEffect(
    () => () => {
      tube.dispose();
      halo.dispose();
    },
    [tube, halo],
  );
  const pulses = useRef<THREE.Group>(null);
  const N = 3;
  useFrame(({ clock }) => {
    const g = pulses.current;
    if (!g) return;
    const t = clock.elapsedTime * 0.16;
    g.children.forEach((child, i) => {
      const u = (t + i / N) % 1;
      curve.getPointAt(u, child.position);
      const fade = Math.sin(u * Math.PI);
      child.scale.setScalar(0.6 + fade * 0.6);
    });
  });
  return (
    <group>
      <mesh geometry={tube}>
        <meshBasicMaterial color={LAB.beam} toneMapped={false} />
      </mesh>
      {glow && (
        <mesh geometry={halo}>
          <meshBasicMaterial color={LAB.beamGlow} transparent opacity={0.16} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
        </mesh>
      )}
      <group ref={pulses}>
        {Array.from({ length: N }, (_, i) => (
          <group key={i}>
            <mesh>
              <sphereGeometry args={[0.22, 12, 12]} />
              <meshBasicMaterial color="#fff1dc" toneMapped={false} />
            </mesh>
            {glow && (
              <sprite scale={[1.6, 1.6, 1]}>
                <spriteMaterial map={glowTexture()} color={LAB.beam} transparent opacity={0.5} depthWrite={false} blending={THREE.AdditiveBlending} />
              </sprite>
            )}
          </group>
        ))}
      </group>
    </group>
  );
}

/** 세로 화면에서는 카메라를 뒤로 빼 전체 구성(항성·행성 3·빔)이 들어오게 한다 */
function FitCamera() {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.viewport.aspect);
  useEffect(() => {
    const k = Math.max(1, 1.75 / aspect);
    camera.position.set(1.5 * k, 2.6 * k, 17 * k);
    camera.lookAt(0.5, 0, 0);
  }, [camera, aspect]);
  return null;
}

/* ------------------------------ 씬 ------------------------------ */
const STAR: [number, number, number] = [-9.5, 1.5, -5];
const P_TEAL: [number, number, number] = [0, 0, 0];
const P_VIOLET: [number, number, number] = [7.2, 1.6, -2.5];
const P_BLUE: [number, number, number] = [3.6, -3.4, 3.2];

export default function ArtScene({ options }: { options: LabOptions }) {
  const { steps, glow, spin } = options;
  return (
    <Canvas flat dpr={[1, 2]} camera={{ position: [1.5, 2.6, 17], fov: 38 }} gl={{ antialias: true, alpha: false }} onCreated={({ gl }) => gl.setClearColor(LAB.bg, 1)}>
      <FitCamera />
      <Backdrop />
      <ambientLight intensity={0.55} color="#c9d4ff" />
      <hemisphereLight args={["#9ab3ff", "#3a1f5c", 0.6]} />
      <directionalLight position={[-6, 8, 7]} intensity={2.4} color="#fff3e0" />

      <Star position={STAR} radius={2.6} steps={steps} glow={glow} />
      <Planet position={P_TEAL} radius={1.7} palette={LAB.planets.teal} seed={1.3} variant="land" steps={steps} spin={spin} tag={{ url: "/company-logos/005930.png", monogram: "삼" }} />
      <Planet position={P_VIOLET} radius={1.25} palette={LAB.planets.violet} seed={4.1} variant="ring" steps={steps} spin={spin} tag={{ url: "/company-logos/000660.png", monogram: "SK" }} />
      <Planet position={P_BLUE} radius={1.05} palette={LAB.planets.blue} seed={7.7} variant="moon" steps={steps} spin={spin} tag={{ url: "/company-logos/NVDA.png", monogram: "N" }} />

      <Beam from={[1.6, 0.4, 0.2]} to={[6.0, 1.5, -2.2]} glow={glow} />
      <Beam from={[3.4, -2.3, 3.0]} to={[0.6, -1.3, 0.9]} glow={glow} bow={0.18} />

      <OrbitControls enablePan={false} enableDamping dampingFactor={0.08} minDistance={6} maxDistance={60} autoRotate={spin} autoRotateSpeed={0.35} target={[0.5, 0, 0]} />
      {glow && (
        <EffectComposer multisampling={4}>
          <Bloom mipmapBlur intensity={0.35} luminanceThreshold={0.85} luminanceSmoothing={0.2} radius={0.5} />
        </EffectComposer>
      )}
    </Canvas>
  );
}
