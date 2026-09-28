import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { seededRandom, useDotTexture } from './textures';


/** 아주 먼 배경 별 — 카메라를 따라다니며 시차(parallax)만 준다 */
export default function Starfield({ count = 5200 }) {
  const dot = useDotTexture();
  const ref = useRef();

  const { positions, colors, sizes } = useMemo(() => {
    const r = seededRandom(4242);
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);
    const sizes = new Float32Array(count);
    const c = new THREE.Color();
    for (let i = 0; i < count; i++) {
      // 구 껍질에 균등 분포
      const u = r() * 2 - 1;
      const th = r() * Math.PI * 2;
      const s = Math.sqrt(1 - u * u);
      const rad = 320 + r() * 460;
      positions[i * 3] = Math.cos(th) * s * rad;
      positions[i * 3 + 1] = u * rad * 0.72;
      positions[i * 3 + 2] = Math.sin(th) * s * rad;

      const warm = r();
      c.setHSL(warm < 0.72 ? 0.58 + r() * 0.06 : 0.08 + r() * 0.05, 0.55, 0.6 + r() * 0.35);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
      sizes[i] = 0.6 + Math.pow(r(), 3) * 4.6;
    }
    return { positions, colors, sizes };
  }, [count]);

  useFrame((state, dt) => {
    if (!ref.current) return;
    ref.current.rotation.y += dt * 0.004;
  });

  return (
    <points ref={ref} frustumCulled={false}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
        <bufferAttribute attach="attributes-aSize" args={[sizes, 1]} />
      </bufferGeometry>
      <shaderMaterial
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        uniforms={{ uMap: { value: dot } }}
        vertexShader={/* glsl */ `
          attribute float aSize;
          varying vec3 vColor;
          void main(){
            vColor = color;
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            gl_PointSize = aSize * (420.0 / -mv.z);
            gl_Position = projectionMatrix * mv;
          }
        `}
        fragmentShader={/* glsl */ `
          uniform sampler2D uMap;
          varying vec3 vColor;
          void main(){
            vec4 t = texture2D(uMap, gl_PointCoord);
            gl_FragColor = vec4(vColor * t.a * 1.35, t.a);
          }
        `}
        vertexColors
      />
    </points>
  );
}
