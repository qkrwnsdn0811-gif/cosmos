import { useRef, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import LabelSprite from './LabelSprite';
import { useDotTexture } from './textures';
import { SECTORS } from '../data/universe';

/**
 * 기업 = 항성.
 *  - 코어 구체 + 가산합성 헤일로 + 회전 링
 *  - 현재 시점에 뉴스가 있으면 충격파 링이 퍼져나간다 (호재=앰버 / 악재=시안)
 */
export default function CompanyNode({
  company,
  position,
  radius,
  label,
  dim = 1,
  active = false,
  focused = false,
  news = null, // { tone, ageDays }
  onHover,
  onClick,
  showLabel = true,
}) {
  const grp = useRef();
  const halo = useRef();
  const ring = useRef();
  const shock = useRef();
  const dot = useDotTexture();
  const color = SECTORS[company.sector].color;
  const seed = useMemo(() => (parseInt(company.id, 10) % 997) / 997, [company.id]);

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime;
    if (grp.current) {
      const target = active ? 1.22 : 1;
      grp.current.scale.lerp({ x: target, y: target, z: target }, Math.min(1, dt * 8));
      grp.current.position.y = position[1] + Math.sin(t * 0.5 + seed * 9) * 0.35;
    }
    if (halo.current) {
      const pulse = 1 + Math.sin(t * 1.4 + seed * 12) * 0.06;
      const s = radius * (active ? 7.4 : 5.4) * pulse;
      halo.current.scale.set(s, s, 1);
      halo.current.material.opacity = (active ? 0.9 : 0.5) * dim;
    }
    if (ring.current) {
      ring.current.rotation.z += dt * 0.35;
      ring.current.rotation.x = Math.PI / 2.6 + Math.sin(t * 0.25 + seed * 5) * 0.22;
      ring.current.material.opacity = (active ? 0.85 : 0.32) * dim;
    }
    if (shock.current) {
      if (news) {
        // 뉴스 발생 후 경과일에 따라 퍼지는 충격파
        const k = THREE.MathUtils.clamp(news.ageDays / 12, 0, 1);
        const s = radius * (1.4 + k * 7.5);
        shock.current.scale.set(s, s, s);
        shock.current.material.opacity = (1 - k) * 0.75 * dim;
        shock.current.visible = true;
      } else {
        shock.current.visible = false;
      }
    }
  });

  return (
    <group ref={grp} position={position}>
      {/* 헤일로 */}
      <sprite ref={halo}>
        <spriteMaterial
          map={dot}
          color={color}
          transparent
          opacity={0.6}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </sprite>

      {/* 코어 */}
      <mesh>
        <icosahedronGeometry args={[radius, 3]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
      <mesh scale={1.35}>
        <icosahedronGeometry args={[radius, 2]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.22 * dim}
          wireframe
          toneMapped={false}
        />
      </mesh>

      {/* 궤도 링 */}
      <mesh ref={ring}>
        <torusGeometry args={[radius * 2.1, radius * 0.045, 8, 72]} />
        <meshBasicMaterial
          color={color}
          transparent
          opacity={0.32}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>

      {/* 뉴스 충격파 */}
      <mesh ref={shock} visible={false}>
        <sphereGeometry args={[1, 24, 16]} />
        <meshBasicMaterial
          color={news?.tone > 0 ? '#ffb04a' : '#54e0ff'}
          transparent
          opacity={0}
          wireframe
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>

      {showLabel && (
        <LabelSprite
          text={label ?? company.name}
          color={active ? '#ffffff' : '#cfe0f5'}
          size={active ? 2.1 : 1.7}
          opacity={(active ? 1 : 0.8) * dim}
          position={[0, radius * 2.9 + 1.4, 0]}
        />
      )}

      {/* 클릭 판정용 (보이지 않는 큰 구) */}
      <mesh
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover?.(company.id);
        }}
        onPointerOut={(e) => {
          e.stopPropagation();
          onHover?.(null);
        }}
        onClick={(e) => {
          e.stopPropagation();
          onClick?.(company.id);
        }}
      >
        <sphereGeometry args={[Math.max(radius * 2.4, 2.6), 12, 8]} />
        <meshBasicMaterial visible={false} />
      </mesh>

      {focused && (
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <ringGeometry args={[radius * 3.1, radius * 3.28, 64]} />
          <meshBasicMaterial
            color="#ffffff"
            transparent
            opacity={0.5}
            side={THREE.DoubleSide}
            toneMapped={false}
          />
        </mesh>
      )}
    </group>
  );
}
