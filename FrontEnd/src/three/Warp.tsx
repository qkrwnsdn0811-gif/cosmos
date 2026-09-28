import { useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { seededRandom } from "@/lib/rng";

const tunnelVert = /* glsl */ `
  varying vec2 vUv;
  void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;
const tunnelFrag = /* glsl */ `
  varying vec2 vUv;
  uniform float uTime;
  uniform float uIntensity;
  float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
  float noise(vec2 p){
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1,0)), u.x), mix(hash(i + vec2(0,1)), hash(i + vec2(1,1)), u.x), u.y);
  }
  float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++){ v += a * noise(p); p *= 2.06; a *= 0.5; } return v; }
  void main(){
    vec2 p = vec2(vUv.x * 4.0, vUv.y * 2.4 - uTime * 0.9);
    float n  = fbm(p * 3.0);
    float n2 = fbm(p * 7.5 + n * 1.6);
    float streak = pow(max(n2, 0.0), 1.5);
    vec3 cold = vec3(0.30, 0.45, 1.00);
    vec3 warm = vec3(0.78, 0.60, 1.00);
    vec3 col = mix(cold, warm, smoothstep(0.32, 0.86, n));
    col += vec3(1.0, 0.97, 0.95) * pow(streak, 3.5) * 0.9;
    float fade = smoothstep(0.0, 0.18, vUv.y) * smoothstep(1.0, 0.70, vUv.y);
    float a = streak * fade * uIntensity * 0.6;
    gl_FragColor = vec4(col * streak * 1.15, a);
  }
`;

type IntensityRef = MutableRefObject<number>;

function Tunnel({ intensityRef }: { intensityRef: IntensityRef }) {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const uniforms = useMemo(() => ({ uTime: { value: 0 }, uIntensity: { value: 0 } }), []);
  useFrame((state) => {
    if (!mat.current) return;
    mat.current.uniforms.uTime.value = state.clock.elapsedTime;
    mat.current.uniforms.uIntensity.value = intensityRef.current;
  });
  return (
    <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -80]} frustumCulled={false} raycast={() => null}>
      <cylinderGeometry args={[40, 40, 220, 64, 1, true]} />
      <shaderMaterial ref={mat} vertexShader={tunnelVert} fragmentShader={tunnelFrag} uniforms={uniforms} side={THREE.BackSide} transparent depthWrite={false} depthTest={false} blending={THREE.AdditiveBlending} toneMapped={false} />
    </mesh>
  );
}

const COUNT = 520;
function Streaks({ intensityRef }: { intensityRef: IntensityRef }) {
  const geo = useRef<THREE.BufferGeometry>(null);
  const mat = useRef<THREE.LineBasicMaterial>(null);
  const { positions, colors, state } = useMemo(() => {
    const positions = new Float32Array(COUNT * 6);
    const colors = new Float32Array(COUNT * 6);
    const state = new Float32Array(COUNT * 3);
    const c = new THREE.Color();
    const rnd = seededRandom(9137);
    for (let i = 0; i < COUNT; i += 1) {
      const ang = rnd() * Math.PI * 2;
      const rad = 2.5 + Math.pow(rnd(), 0.6) * 34;
      state[i * 3] = Math.cos(ang) * rad;
      state[i * 3 + 1] = Math.sin(ang) * rad;
      state[i * 3 + 2] = -rnd() * 300;
      const h = rnd() < 0.7 ? 0.62 + rnd() * 0.08 : 0.72 + rnd() * 0.06;
      c.setHSL(h, 0.75, 0.68);
      for (let k = 0; k < 2; k += 1) {
        colors[i * 6 + k * 3] = c.r;
        colors[i * 6 + k * 3 + 1] = c.g;
        colors[i * 6 + k * 3 + 2] = c.b;
      }
    }
    return { positions, colors, state };
  }, []);

  /* eslint-disable react-hooks/immutability -- R3F advances particle buffers in place between React renders. */
  useFrame((_, dt) => {
    const it = intensityRef.current;
    if (mat.current) mat.current.opacity = it * 0.55;
    if (it < 0.01 || !geo.current) return;
    const step = Math.min(dt, 0.05) * (45 + 260 * it);
    const len = 2 + 16 * it;
    for (let i = 0; i < COUNT; i += 1) {
      let z = state[i * 3 + 2] + step;
      if (z > 12) z = -300 - Math.random() * 60;
      state[i * 3 + 2] = z;
      const x = state[i * 3];
      const y = state[i * 3 + 1];
      positions[i * 6] = x;
      positions[i * 6 + 1] = y;
      positions[i * 6 + 2] = z;
      positions[i * 6 + 3] = x;
      positions[i * 6 + 4] = y;
      positions[i * 6 + 5] = z - len;
    }
    geo.current.attributes.position.needsUpdate = true;
  });

  /* eslint-enable react-hooks/immutability */

  return (
    <lineSegments frustumCulled={false} raycast={() => null}>
      <bufferGeometry ref={geo}>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <lineBasicMaterial ref={mat} vertexColors transparent opacity={0} depthWrite={false} depthTest={false} blending={THREE.AdditiveBlending} toneMapped={false} />
    </lineSegments>
  );
}

/** 카메라에 붙어 함께 움직이는 워프 이펙트. intensityRef 는 Director 가 매 프레임 갱신한다. */
export default function Warp({ intensityRef }: { intensityRef: IntensityRef }) {
  const grp = useRef<THREE.Group>(null);
  useFrame((state) => {
    if (!grp.current) return;
    grp.current.position.copy(state.camera.position);
    grp.current.quaternion.copy(state.camera.quaternion);
    grp.current.visible = intensityRef.current > 0.004;
  });
  return (
    <group ref={grp} renderOrder={50}>
      <Tunnel intensityRef={intensityRef} />
      <Streaks intensityRef={intensityRef} />
    </group>
  );
}
