import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { seededRandom } from './textures';

/* ------------------------------------------------------------------ */
/* 웜홀 터널 — 카메라를 감싸는 원통에 흐르는 fbm 셰이더                   */
/* ------------------------------------------------------------------ */
const tunnelVert = /* glsl */ `
  varying vec2 vUv;
  void main(){
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const tunnelFrag = /* glsl */ `
  varying vec2 vUv;
  uniform float uTime;
  uniform float uIntensity;

  float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123); }
  float noise(vec2 p){
    vec2 i = floor(p), f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1,0)), u.x),
               mix(hash(i + vec2(0,1)), hash(i + vec2(1,1)), u.x), u.y);
  }
  float fbm(vec2 p){
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 5; i++){ v += a * noise(p); p *= 2.06; a *= 0.5; }
    return v;
  }

  void main(){
    // x = 원주(seam 이 생기지 않도록 4배로 타일), y = 진행 방향
    vec2 p = vec2(vUv.x * 4.0, vUv.y * 2.4 - uTime * 0.9);
    float n  = fbm(p * 3.0);
    float n2 = fbm(p * 7.5 + n * 1.6);
    float streak = pow(max(n2, 0.0), 1.5);

    vec3 cold = vec3(0.14, 0.42, 1.00);
    vec3 warm = vec3(1.00, 0.60, 0.22);
    vec3 col = mix(cold, warm, smoothstep(0.32, 0.86, n));
    col += vec3(1.0, 0.95, 0.88) * pow(streak, 3.5) * 0.9;

    // 터널 양끝을 부드럽게 지운다 (원통 경계가 보이지 않도록)
    float fade = smoothstep(0.0, 0.18, vUv.y) * smoothstep(1.0, 0.70, vUv.y);
    float a = streak * fade * uIntensity * 0.6;
    gl_FragColor = vec4(col * streak * 1.15, a);
  }
`;

function Tunnel({ intensityRef }) {
  const mat = useRef();
  useFrame((state) => {
    if (!mat.current) return;
    mat.current.uniforms.uTime.value = state.clock.elapsedTime;
    mat.current.uniforms.uIntensity.value = intensityRef.current;
  });
  return (
    <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -80]} frustumCulled={false}>
      <cylinderGeometry args={[40, 40, 220, 64, 1, true]} />
      <shaderMaterial
        ref={mat}
        vertexShader={tunnelVert}
        fragmentShader={tunnelFrag}
        uniforms={{ uTime: { value: 0 }, uIntensity: { value: 0 } }}
        side={THREE.BackSide}
        transparent
        depthWrite={false}
        depthTest={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </mesh>
  );
}

/* ------------------------------------------------------------------ */
/* 하이퍼스페이스 스트릭 — 카메라 공간에서 뒤로 흐르는 선분들             */
/* ------------------------------------------------------------------ */
const COUNT = 520;

function Streaks({ intensityRef }) {
  const geo = useRef();
  const mat = useRef();

  const { positions, colors, state } = useMemo(() => {
    const positions = new Float32Array(COUNT * 6);
    const colors = new Float32Array(COUNT * 6);
    const state = new Float32Array(COUNT * 3); // x, y, z
    const c = new THREE.Color();
    const rnd = seededRandom(9137);
    for (let i = 0; i < COUNT; i++) {
      const ang = rnd() * Math.PI * 2;
      const rad = 2.5 + Math.pow(rnd(), 0.6) * 34;
      state[i * 3] = Math.cos(ang) * rad;
      state[i * 3 + 1] = Math.sin(ang) * rad;
      state[i * 3 + 2] = -rnd() * 300;

      const h = rnd() < 0.7 ? 0.56 + rnd() * 0.06 : 0.08 + rnd() * 0.04;
      c.setHSL(h, 0.75, 0.62);
      for (let k = 0; k < 2; k++) {
        colors[i * 6 + k * 3] = c.r;
        colors[i * 6 + k * 3 + 1] = c.g;
        colors[i * 6 + k * 3 + 2] = c.b;
      }
    }
    return { positions, colors, state };
  }, []);

  useFrame((_, dt) => {
    const it = intensityRef.current;
    if (mat.current) mat.current.opacity = it * 0.55;
    if (it < 0.01 || !geo.current) return;
    const step = Math.min(dt, 0.05) * (45 + 260 * it);
    const len = 2 + 16 * it;
    for (let i = 0; i < COUNT; i++) {
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

  return (
    <lineSegments frustumCulled={false}>
      <bufferGeometry ref={geo}>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <lineBasicMaterial
        ref={mat}
        vertexColors
        transparent
        opacity={0}
        depthWrite={false}
        depthTest={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </lineSegments>
  );
}

/**
 * 카메라에 붙어서 함께 움직이는 워프 이펙트 묶음.
 * intensityRef 는 Director 가 매 프레임 갱신한다 (0 = 꺼짐, 1 = 최대).
 */
export default function Warp({ intensityRef }) {
  const grp = useRef();
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
