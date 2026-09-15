import { useEffect, useMemo } from "react";
import * as THREE from "three";
import { LAB, glowTexture } from "./toon";
import { dotTexture } from "./textures";

/**
 * 장난감 우주 배경 — 안쪽을 보는 큰 구에 네이비 그라디언트, 성운 두 덩이, 플랫한 별.
 * 어느 각도로 돌려도 가장자리가 없고, 안개·톤매핑의 영향을 받지 않는다.
 * radius: 씬 크기에 맞춘 배경 반지름 (카메라가 이 안에 있어야 한다)
 */
export default function Backdrop({ radius = 150, stars = 260 }: { radius?: number; stars?: number }) {
  const geo = useMemo(() => {
    const n = stars;
    const pos = new Float32Array(n * 3);
    const size = new Float32Array(n);
    let s = 7;
    const rnd = () => {
      s = (s * 16807) % 2147483647;
      return s / 2147483647;
    };
    for (let i = 0; i < n; i += 1) {
      const r = radius * (0.55 + rnd() * 0.3);
      const th = rnd() * Math.PI * 2;
      const ph = Math.acos(2 * rnd() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.cos(ph) * 0.8;
      pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
      size[i] = 0.9 + rnd() * rnd() * 3.0; // 화면 픽셀 크기
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    return g;
  }, [radius, stars]);
  useEffect(() => () => geo.dispose(), [geo]);
  const uniforms = useMemo(() => ({ uMap: { value: dotTexture() }, uRadius: { value: radius } }), [radius]);
  const k = radius / 90;
  return (
    <group>
      <mesh scale={[radius, radius, radius]} raycast={() => null}>
        <sphereGeometry args={[1, 24, 16]} />
        <shaderMaterial
          side={THREE.BackSide}
          depthWrite={false}
          uniforms={{ uTop: { value: new THREE.Color(LAB.bgTop) }, uBottom: { value: new THREE.Color(LAB.bg) } }}
          vertexShader={/* glsl */ `varying vec3 vP; void main(){ vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`}
          fragmentShader={/* glsl */ `uniform vec3 uTop; uniform vec3 uBottom; varying vec3 vP;
            void main(){ float t = smoothstep(-0.6, 0.8, vP.y); gl_FragColor = vec4(mix(uBottom, uTop, t), 1.0); }`}
        />
      </mesh>
      <sprite position={[-18 * k, 6 * k, -40 * k]} scale={[70 * k, 46 * k, 1]} raycast={() => null}>
        <spriteMaterial map={glowTexture()} color={LAB.nebulaA} transparent opacity={0.22} depthWrite={false} blending={THREE.AdditiveBlending} fog={false} />
      </sprite>
      <sprite position={[22 * k, -8 * k, -45 * k]} scale={[64 * k, 40 * k, 1]} raycast={() => null}>
        <spriteMaterial map={glowTexture()} color={LAB.nebulaB} transparent opacity={0.16} depthWrite={false} blending={THREE.AdditiveBlending} fog={false} />
      </sprite>
      <points geometry={geo} frustumCulled={false} raycast={() => null}>
        <shaderMaterial
          transparent
          depthWrite={false}
          uniforms={uniforms}
          vertexShader={/* glsl */ `attribute float aSize; uniform float uRadius; void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); gl_PointSize = aSize * clamp(uRadius * 0.7 / -mv.z, 0.6, 1.6); gl_Position = projectionMatrix * mv; }`}
          fragmentShader={/* glsl */ `uniform sampler2D uMap; void main(){ vec4 t = texture2D(uMap, gl_PointCoord); gl_FragColor = vec4(vec3(1.0), t.a * 0.9); }`}
        />
      </points>
    </group>
  );
}
