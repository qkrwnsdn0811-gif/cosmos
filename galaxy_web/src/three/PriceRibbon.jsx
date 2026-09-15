import { useMemo, useRef, useState } from 'react';
import { useFrame } from '@react-three/fiber';
import { Html } from '@react-three/drei';
import * as THREE from 'three';
import LabelSprite from './LabelSprite';
import { N_DAYS, SECTORS, COMPANY_BY_ID, dateLabel, newsFor, normalizedPrice } from '../data/universe';

const W = 64; // 가로 폭 (world units)
const H = 13; // 세로 스케일

const ribbonVert = /* glsl */ `
  attribute float aT;
  attribute float aEdge;
  varying float vT;
  varying float vEdge;
  void main(){
    vT = aT;
    vEdge = aEdge;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const ribbonFrag = /* glsl */ `
  uniform float uProgress;
  uniform vec3 uColor;
  varying float vT;
  varying float vEdge;
  void main(){
    // 아직 도달하지 않은 미래 구간은 거의 지운다
    float future = step(uProgress, vT);
    float a = mix(0.55, 0.05, future);
    a *= mix(0.06, 1.0, vEdge);      // 아래(기준선)로 갈수록 투명
    vec3 col = mix(uColor, uColor * 1.9, vEdge);
    gl_FragColor = vec4(col, a);
  }
`;

/**
 * 기업의 정규화 주가 경로를 3D 면적 차트("리본")로 그린다.
 * 현재 시점(uProgress) 이후 구간은 흐리게 처리해 시간 여행 느낌을 준다.
 */
export default function PriceRibbon({ companyId, day, onNewsHover }) {
  const areaMat = useRef();
  const lineMat = useRef();
  const cursor = useRef();
  const company = COMPANY_BY_ID[companyId];
  const color = SECTORS[company.sector].color;

  const { areaGeo, lineGeo, series, yScale, news } = useMemo(() => {
    const series = normalizedPrice(companyId, 0, N_DAYS - 1);
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of series) {
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    const span = Math.max(0.25, hi - lo);
    const yScale = H / span;
    const yOf = (v) => (v - lo) * yScale - H * 0.5;

    const STEP = 2; // 2영업일마다 샘플 → 250 포인트
    const n = Math.floor((N_DAYS - 1) / STEP) + 1;

    // 면적: 삼각형 스트립 (위=선, 아래=기준선)
    const pos = new Float32Array(n * 2 * 3);
    const aT = new Float32Array(n * 2);
    const aEdge = new Float32Array(n * 2);
    const linePos = new Float32Array(n * 3);
    const lineT = new Float32Array(n);
    const lineEdge = new Float32Array(n).fill(1);

    const base = -H * 0.5 - 1.5;
    for (let i = 0; i < n; i++) {
      const d = Math.min(N_DAYS - 1, i * STEP);
      const x = -W / 2 + (W * d) / (N_DAYS - 1);
      const y = yOf(series[d]);
      const t = d / (N_DAYS - 1);

      pos[i * 6] = x;
      pos[i * 6 + 1] = y;
      pos[i * 6 + 2] = 0;
      aT[i * 2] = t;
      aEdge[i * 2] = 1;

      pos[i * 6 + 3] = x;
      pos[i * 6 + 4] = base;
      pos[i * 6 + 5] = 0;
      aT[i * 2 + 1] = t;
      aEdge[i * 2 + 1] = 0;

      linePos[i * 3] = x;
      linePos[i * 3 + 1] = y;
      linePos[i * 3 + 2] = 0.02;
      lineT[i] = t;
    }

    const index = [];
    for (let i = 0; i < n - 1; i++) {
      const a = i * 2;
      index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }

    const areaGeo = new THREE.BufferGeometry();
    areaGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    areaGeo.setAttribute('aT', new THREE.BufferAttribute(aT, 1));
    areaGeo.setAttribute('aEdge', new THREE.BufferAttribute(aEdge, 1));
    areaGeo.setIndex(index);

    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
    lineGeo.setAttribute('aT', new THREE.BufferAttribute(lineT, 1));
    lineGeo.setAttribute('aEdge', new THREE.BufferAttribute(lineEdge, 1));

    return { areaGeo, lineGeo, series, yScale, lo, news: newsFor(companyId) };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  const yOf = useMemo(() => {
    let lo = Infinity;
    for (const v of series) if (v < lo) lo = v;
    return (v) => (v - lo) * yScale - H * 0.5;
  }, [series, yScale]);

  const [hover, setHover] = useState(null);

  // 마커 좌표를 미리 계산해 두면 렌더/툴팁에서 같은 값을 쓴다
  const visibleNews = useMemo(
    () =>
      news
        .filter((n) => n.day <= day)
        .map((n) => ({
          ...n,
          x: -W / 2 + (W * n.day) / (N_DAYS - 1),
          y: yOf(series[n.day]),
        })),
    [news, day, yOf, series]
  );

  const progress = day / (N_DAYS - 1);
  const cursorX = -W / 2 + W * progress;

  useFrame((state) => {
    if (areaMat.current) areaMat.current.uniforms.uProgress.value = progress;
    if (lineMat.current) lineMat.current.uniforms.uProgress.value = progress;
    if (cursor.current) {
      cursor.current.material.opacity = 0.5 + Math.sin(state.clock.elapsedTime * 3) * 0.18;
    }
  });

  const uniforms = useMemo(
    () => ({ uProgress: { value: 0 }, uColor: { value: new THREE.Color(color) } }),
    [color]
  );
  const lineUniforms = useMemo(
    () => ({ uProgress: { value: 0 }, uColor: { value: new THREE.Color(color) } }),
    [color]
  );

  return (
    <group>
      {/* 기준선 */}
      <mesh position={[0, yOf(0), -0.05]}>
        <planeGeometry args={[W, 0.06]} />
        <meshBasicMaterial color="#5d738f" transparent opacity={0.45} toneMapped={false} />
      </mesh>

      {/* 면적 */}
      <mesh geometry={areaGeo} frustumCulled={false}>
        <shaderMaterial
          ref={areaMat}
          uniforms={uniforms}
          vertexShader={ribbonVert}
          fragmentShader={ribbonFrag}
          transparent
          depthWrite={false}
          side={THREE.DoubleSide}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>

      {/* 상단 라인 */}
      <line geometry={lineGeo} frustumCulled={false}>
        <shaderMaterial
          ref={lineMat}
          uniforms={lineUniforms}
          vertexShader={ribbonVert}
          fragmentShader={ribbonFrag}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </line>

      {/* 현재 시점 커서 */}
      <mesh ref={cursor} position={[cursorX, 0, 0.1]}>
        <planeGeometry args={[0.14, H * 1.7]} />
        <meshBasicMaterial
          color="#ffffff"
          transparent
          opacity={0.6}
          depthWrite={false}
          toneMapped={false}
        />
      </mesh>
      <LabelSprite
        text={dateLabel(day)}
        color="#ffffff"
        size={1.5}
        px={40}
        position={[cursorX, H * 0.95, 0.2]}
      />

      {/* 뉴스 마커 — 발생일 위치에 세워지는 빛기둥 */}
      {visibleNews.map((n) => {
        const c = n.tone > 0 ? '#ffb04a' : '#54e0ff';
        const fresh = Math.max(0, 1 - (day - n.day) / 60);
        const on = hover?.id === n.id;
        return (
          <group key={n.id} position={[n.x, n.y, 0.05]}>
            <mesh position={[0, 3.2, 0]}>
              <planeGeometry args={[0.09, 6.4]} />
              <meshBasicMaterial
                color={c}
                transparent
                opacity={(on ? 0.9 : 0.25) + fresh * 0.5}
                depthWrite={false}
                blending={THREE.AdditiveBlending}
                toneMapped={false}
              />
            </mesh>
            <mesh position={[0, 6.6, 0]} scale={on ? 1.5 : 1}>
              <octahedronGeometry args={[0.6 + n.mag * 0.06, 0]} />
              <meshBasicMaterial color={c} toneMapped={false} />
            </mesh>
            {/* 판정용 — 마커가 화면에서 매우 작으므로 넉넉한 투명 구를 둔다 */}
            <mesh
              position={[0, 6.6, 0]}
              onPointerOver={(e) => {
                e.stopPropagation();
                setHover(n);
                onNewsHover?.(n);
              }}
              onPointerOut={(e) => {
                e.stopPropagation();
                setHover((h) => (h?.id === n.id ? null : h));
                onNewsHover?.(null);
              }}
            >
              <sphereGeometry args={[2.6, 10, 8]} />
              <meshBasicMaterial visible={false} />
            </mesh>
          </group>
        );
      })}

      {/* 툴팁 — 호버한 마커 바로 위에 뜬다 */}
      {hover && (
        <Html position={[hover.x, hover.y + 10, 0.4]} center style={{ pointerEvents: 'none' }}>
          <div className={`news-tip ${hover.tone > 0 ? 'good' : 'bad'}`}>
            <span className="tag">{hover.tone > 0 ? '호재' : '악재'}</span>
            <span className="head">{hover.headline}</span>
            <span className="impact">
              이후 5영업일{' '}
              <b className={hover.impact >= 0 ? 'up' : 'down'}>
                {hover.impact >= 0 ? '+' : ''}
                {(hover.impact * 100).toFixed(1)}%
              </b>
            </span>
          </div>
        </Html>
      )}

      {/* 축 라벨 */}
      <LabelSprite
        text={dateLabel(0)}
        color="#7b8ba6"
        size={1.25}
        px={36}
        position={[-W / 2, -H * 0.72, 0]}
      />
      <LabelSprite
        text={dateLabel(N_DAYS - 1)}
        color="#7b8ba6"
        size={1.25}
        px={36}
        position={[W / 2, -H * 0.72, 0]}
      />
      <LabelSprite
        text={`${company.name} · 기간 수익률 ${(series[day] * 100).toFixed(1)}%`}
        color={color}
        size={1.9}
        px={44}
        weight={700}
        position={[-W / 2 + 11, H * 0.95, 0]}
      />
    </group>
  );
}
