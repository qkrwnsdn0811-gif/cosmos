import { useMemo, useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import CompanyNode from './CompanyNode';
import RelationLines from './RelationLines';
import LabelSprite from './LabelSprite';
import PriceRibbon from './PriceRibbon';
import { useDotTexture } from './textures';
import { useGalaxy } from '../store/useGalaxy';
import {
  ALL_NEWS,
  COMPANY_BY_ID,
  REL_TYPES,
  SECTORS,
  neighborsOf,
  nodeRadius,
} from '../data/universe';

/** 중심 항성 — 선택된 기업 */
function CentralStar({ company }) {
  const inner = useRef();
  const shellA = useRef();
  const shellB = useRef();
  const halo = useRef();
  const dot = useDotTexture();
  const color = SECTORS[company.sector].color;

  useFrame((state, dt) => {
    const t = state.clock.elapsedTime;
    if (inner.current) inner.current.rotation.y += dt * 0.12;
    if (shellA.current) {
      shellA.current.rotation.y -= dt * 0.22;
      shellA.current.rotation.x += dt * 0.07;
    }
    if (shellB.current) {
      shellB.current.rotation.z += dt * 0.16;
      shellB.current.rotation.x -= dt * 0.05;
    }
    if (halo.current) {
      const s = 30 + Math.sin(t * 1.1) * 1.6;
      halo.current.scale.set(s, s, 1);
    }
  });

  return (
    <group>
      <sprite ref={halo}>
        <spriteMaterial
          map={dot}
          color={color}
          transparent
          opacity={0.75}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </sprite>
      <mesh ref={inner}>
        <icosahedronGeometry args={[3.1, 4]} />
        <meshBasicMaterial color="#ffffff" toneMapped={false} />
      </mesh>
      <mesh ref={shellA} scale={1.5}>
        <icosahedronGeometry args={[3.1, 2]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={0.5} toneMapped={false} />
      </mesh>
      <mesh ref={shellB} scale={2.1}>
        <icosahedronGeometry args={[3.1, 1]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={0.22} toneMapped={false} />
      </mesh>
      <LabelSprite
        text={company.name}
        color="#ffffff"
        size={3.4}
        px={56}
        weight={700}
        position={[0, 9.2, 0]}
      />
      <LabelSprite
        text={`${company.ticker} · ${SECTORS[company.sector].name}`}
        color={color}
        size={1.6}
        px={40}
        position={[0, 6.8, 0]}
      />
    </group>
  );
}

/** 궤도 원 (얇은 링) */
function OrbitRing({ radius, color, opacity }) {
  return (
    <mesh rotation={[Math.PI / 2, 0, 0]}>
      <ringGeometry args={[radius - 0.035, radius + 0.035, 128]} />
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={THREE.DoubleSide}
        depthWrite={false}
        toneMapped={false}
      />
    </mesh>
  );
}

export default function SystemView() {
  const focus = useGalaxy((s) => s.focus);
  const day = useGalaxy((s) => s.day);
  const hovered = useGalaxy((s) => s.hovered);
  const selectedEdge = useGalaxy((s) => s.selectedEdge);
  const setHovered = useGalaxy((s) => s.setHovered);
  const selectEdge = useGalaxy((s) => s.selectEdge);
  const warpTo = useGalaxy((s) => s.warpTo);
  const showLabels = useGalaxy((s) => s.showLabels);

  const company = COMPANY_BY_ID[focus];

  // 이웃 배치: 상관계수가 높을수록 가까운 궤도, 부호로 위/아래
  const layout = useMemo(() => {
    if (!focus) return [];
    const ns = neighborsOf(focus, day, { threshold: 0.28, limit: 9 });
    return ns.map((n, i) => {
      const a = Math.abs(n.corr);
      const radius = 34 - Math.min(0.85, a) * 18; // 0.85 → 19, 0.3 → 28.6
      const ang = (i / Math.max(1, ns.length)) * Math.PI * 2 + 0.4;
      const y = (n.corr >= 0 ? 1 : -1) * (2 + (1 - a) * 8);
      return {
        ...n,
        radius,
        angle: ang,
        position: [Math.cos(ang) * radius, y, Math.sin(ang) * radius],
      };
    });
  }, [focus, day]);

  const lineEdges = useMemo(
    () =>
      layout.map((n) => {
        const key = focus < n.id ? `${focus}|${n.id}` : `${n.id}|${focus}`;
        const hot = hovered === n.id || selectedEdge === key;
        const anyHot = Boolean(hovered || selectedEdge);
        return {
          key,
          from: [0, 0, 0],
          to: n.position,
          color: n.crossSector && n.type === 'unknown' ? '#ff9de0' : REL_TYPES[n.type].color,
          alpha: anyHot ? (hot ? 1 : 0.1) : 0.28 + Math.min(1, Math.abs(n.corr)) * 0.5,
          seed: (parseInt(n.id, 10) % 977) / 977,
        };
      }),
    [layout, focus, hovered, selectedEdge]
  );

  const recentNews = useMemo(() => {
    const map = {};
    for (const n of ALL_NEWS) {
      const age = day - n.day;
      if (age < 0 || age > 12) continue;
      const prev = map[n.companyId];
      if (!prev || age < prev.ageDays) map[n.companyId] = { tone: n.tone, ageDays: age };
    }
    return map;
  }, [day]);

  if (!company) return null;

  return (
    <group>
      <CentralStar company={company} />
      <RelationLines edges={lineEdges} bow={0.1} />

      {layout.map((n) => {
        const c = COMPANY_BY_ID[n.id];
        const key = focus < n.id ? `${focus}|${n.id}` : `${n.id}|${focus}`;
        const anyHot = Boolean(hovered || selectedEdge);
        const hot = hovered === n.id || selectedEdge === key;
        return (
          <group key={n.id}>
            <OrbitRing
              radius={n.radius}
              color={SECTORS[c.sector].color}
              opacity={hot ? 0.28 : 0.07}
            />
            <CompanyNode
              company={c}
              position={n.position}
              radius={Math.max(1.1, nodeRadius(c) * 0.62)}
              label={`${c.name}  ${n.corr >= 0 ? '+' : ''}${n.corr.toFixed(2)}`}
              dim={anyHot ? (hot ? 1 : 0.28) : 1}
              active={hovered === n.id}
              news={recentNews[n.id] || null}
              showLabel={showLabels}
              onHover={setHovered}
              onClick={() => selectEdge(key)}
            />
            {/* 더블클릭 대신 "이 기업으로 워프" 버튼을 호버 시 노출 */}
            {hovered === n.id && (
              <Html position={[n.position[0], n.position[1] - 3.6, n.position[2]]} center>
                <button
                  className="warp-chip"
                  onClick={(e) => {
                    e.stopPropagation();
                    warpTo(n.id);
                  }}
                >
                  이 기업으로 워프 →
                </button>
              </Html>
            )}
          </group>
        );
      })}

      {/* 주가 리본 */}
      <group position={[0, -36, 6]} rotation={[-0.55, 0, 0]}>
        <PriceRibbon companyId={focus} day={day} />
      </group>
    </group>
  );
}
