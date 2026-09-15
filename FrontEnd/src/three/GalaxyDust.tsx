import { useEffect, useMemo, useRef, type MutableRefObject } from "react";
import { useFrame } from "@react-three/fiber";
import * as THREE from "three";
import { ARMS, ARM_SPREAD, DISC_Y, R_CORE, R_MAX, armTheta } from "@/lib/galaxyShape";
import { gaussian, seededRandom } from "@/lib/rng";
import { isLowEnd } from "./perf";
import { dotTexture } from "./textures";
import { glowTexture } from "./toon";

interface Props {
  /** 배치 시드 — 같은 값이면 같은 별가루가 나온다 */
  seed?: number;
  /** 원반 바깥 반지름. 산업 필터로 은하가 작아지면 별가루도 같이 줄어든다 */
  radius?: number;
  /** 나선 팔을 그릴지. 산업 하나만 남은 필터 뷰는 layoutGalaxy 가 팔 인력을 끄고 행성을 고르게 펴므로 별가루도 균등해야 어긋나지 않는다 */
  spiral?: boolean;
  /** 워프 강도 0~1 — 워프 중에는 별가루가 옅어져 스트릭 연출을 가리지 않는다 */
  warpRef?: MutableRefObject<number>;
}

/** 총 점 수 (저사양 기기는 절반 이하) */
const TOTAL = 9000;
const TOTAL_LOW = 4000;
/**
 * 핵 벌지·팔 사이 배경이 차지하는 몫 (TOTAL 기준, 나머지가 나선 팔).
 * 벌지를 줄이고 팔 사이를 늘려 "중심만 뭉친 덩어리" 가 아니라 원반 전체로 읽히게 한다.
 */
const BULGE_SHARE = 400 / TOTAL;
const INTER_SHARE = 1400 / TOTAL;
/** 반지름 방향 밀도 감쇠 길이 — 바깥으로 갈수록 성기다 (거절 샘플링) */
const FALLOFF = 110;
/** 별가루 층 두께 배율 — 행성보다 살짝 두껍게 퍼뜨려 원반에 부피감을 준다 */
const DUST_Y = 1.4;
/** 워프 중 알파 배율 */
const WARP_ALPHA = 0.4;
/** 전체 밝기 배율 — 별가루가 행성·간선을 덮지 않도록 한 단계 낮춘다 */
const DUST_ALPHA = 0.8;
/** 벌지는 여기에 한 번 더 곱해 은은하게 (핵이 하얗게 뭉개지지 않도록) */
const BULGE_ALPHA = 0.6;
/** 핵 글로우 두 겹의 알파와 지름 배율 (R_CORE 기준) */
const GLOW_INNER_A = 0.14;
const GLOW_OUTER_A = 0.05;
const GLOW_INNER_D = R_CORE * 4.8;
const GLOW_OUTER_D = R_CORE * 10;

const COOL_A = new THREE.Color("#dfe8ff");
const COOL_B = new THREE.Color("#9fb8ff");
const WARM = new THREE.Color("#ffd9a8");
const GLOW = "#cfe0ff";
const tmpColor = new THREE.Color();

/**
 * 나선 은하 별가루 — 은하 뷰 배경 층.
 * 팔의 곡선은 layoutGalaxy 와 같은 galaxyShape.armTheta 를 쓰므로 행성이 정확히 별가루 팔 위에 얹힌다.
 * (spiral=false 인 단일 산업 필터 뷰는 layoutGalaxy 도 팔을 쓰지 않으므로 별가루도 각도를 고르게 편다)
 * 가산 블렌딩·depthWrite off 라 행성과 빔을 가리지 않고, 피킹에서도 빠진다.
 */
export default function GalaxyDust({ seed = 0, radius = R_MAX, spiral = true, warpRef }: Props) {
  const mat = useRef<THREE.ShaderMaterial>(null);
  const glowInner = useRef<THREE.SpriteMaterial>(null);
  const glowOuter = useRef<THREE.SpriteMaterial>(null);

  const geo = useMemo(() => {
    const rDisc = Math.max(R_CORE * 2, radius);
    const total = isLowEnd() ? TOTAL_LOW : TOTAL;
    const bulge = Math.round(total * BULGE_SHARE);
    const inter = Math.round(total * INTER_SHARE);
    const arms = total - bulge - inter;
    const rng = seededRandom((seed >>> 0) ^ 0x9e3779b9);
    // 원반이 줄어들면 감쇠 길이도 같은 비율로 줄여 밀도 분포가 닮은꼴을 유지한다
    const falloff = (FALLOFF * rDisc) / R_MAX;
    const g3 = () => Math.max(-3, Math.min(3, gaussian(rng)));
    /** 밀도 ∝ exp(-r/falloff) 를 거절 샘플링으로 뽑는다 (면적 균등 sqrt 위에 얹는다) */
    const sampleR = () => {
      for (let k = 0; k < 24; k += 1) {
        const r = rDisc * Math.sqrt(rng());
        if (rng() < Math.exp(-r / falloff)) return r;
      }
      return rDisc * Math.sqrt(rng()) * 0.5;
    };

    const pos = new Float32Array(total * 3);
    const col = new Float32Array(total * 3);
    const size = new Float32Array(total);
    const alpha = new Float32Array(total);
    const seedAttr = new Float32Array(total);

    const put = (i: number, x: number, y: number, z: number, px: number, a: number) => {
      pos[i * 3] = x;
      pos[i * 3 + 1] = y;
      pos[i * 3 + 2] = z;
      // 18% 는 따뜻한 별 — 참고 이미지처럼 흰·푸른 별가루 사이에 드문드문 섞인다
      if (rng() < 0.18) tmpColor.copy(WARM);
      else tmpColor.copy(COOL_A).lerp(COOL_B, rng());
      col[i * 3] = tmpColor.r;
      col[i * 3 + 1] = tmpColor.g;
      col[i * 3 + 2] = tmpColor.b;
      size[i] = px;
      alpha[i] = a * DUST_ALPHA;
      seedAttr[i] = rng() * Math.PI * 2;
    };

    let o = 0;
    for (let i = 0; i < arms; i += 1, o += 1) {
      const r = sampleR();
      // 팔은 안쪽에서 가늘고 바깥에서 흩어진다 — 행성 배치와 같은 ARM_SPREAD 를 쓰되 조금 더 퍼뜨려 팔 경계를 흐린다.
      // 팔이 없는 뷰(spiral=false)에서는 행성처럼 원둘레에 고르게 뿌린다
      const th = spiral ? armTheta(Math.floor(rng() * ARMS) % ARMS, r) + g3() * ARM_SPREAD(r) * 1.3 : rng() * Math.PI * 2;
      put(o, Math.cos(th) * r, g3() * DISC_Y(r) * DUST_Y, Math.sin(th) * r, 0.6 + rng() * rng() * 2.0, 0.35 + rng() * 0.35);
    }
    for (let i = 0; i < bulge; i += 1, o += 1) {
      const s = R_CORE * 0.6;
      put(o, g3() * s, g3() * 5, g3() * s, 0.9 + rng() * 1.7, (0.4 + rng() * 0.3) * BULGE_ALPHA);
    }
    for (let i = 0; i < inter; i += 1, o += 1) {
      const r = sampleR();
      const th = rng() * Math.PI * 2;
      put(o, Math.cos(th) * r, g3() * DISC_Y(r) * DUST_Y, Math.sin(th) * r, 0.6 + rng() * 1.2, 0.35 + rng() * 0.15);
    }

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    g.setAttribute("aAlpha", new THREE.BufferAttribute(alpha, 1));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seedAttr, 1));
    return g;
  }, [seed, radius, spiral]);

  useEffect(() => () => geo.dispose(), [geo]);

  const uniforms = useMemo(() => ({ uMap: { value: dotTexture() }, uScale: { value: 270 }, uTime: { value: 0 }, uWarp: { value: 0 } }), []);

  useFrame((state) => {
    const warp = warpRef?.current ?? 0;
    const u = mat.current?.uniforms;
    if (u) {
      u.uTime.value = state.clock.elapsedTime;
      u.uWarp.value = warp;
      // aSize 가 "기준 거리에서의 화면 px" 이 되도록 화면 높이에 맞춘다
      u.uScale.value = state.size.height * 0.3;
    }
    const dim = 1 - (1 - WARP_ALPHA) * warp;
    if (glowInner.current) glowInner.current.opacity = GLOW_INNER_A * dim;
    if (glowOuter.current) glowOuter.current.opacity = GLOW_OUTER_A * dim;
  });

  return (
    <group renderOrder={0}>
      {/* 핵 글로우 — 안쪽 밝은 알맹이 + 바깥으로 번지는 헤일로 */}
      <sprite scale={[GLOW_INNER_D, GLOW_INNER_D, 1]} raycast={() => null} renderOrder={0}>
        <spriteMaterial ref={glowInner} map={glowTexture()} color={GLOW} transparent opacity={GLOW_INNER_A} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} fog={false} />
      </sprite>
      <sprite scale={[GLOW_OUTER_D, GLOW_OUTER_D, 1]} raycast={() => null} renderOrder={0}>
        <spriteMaterial ref={glowOuter} map={glowTexture()} color={GLOW} transparent opacity={GLOW_OUTER_A} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} fog={false} />
      </sprite>
      <points geometry={geo} frustumCulled={false} raycast={() => null} renderOrder={0}>
        <shaderMaterial ref={mat} uniforms={uniforms} vertexShader={DUST_VERTEX} fragmentShader={DUST_FRAGMENT} transparent depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </points>
    </group>
  );
}

const DUST_VERTEX = /* glsl */ `
  attribute vec3 aColor;
  attribute float aSize;
  attribute float aAlpha;
  attribute float aSeed;
  uniform float uScale;
  uniform float uTime;
  uniform float uWarp;
  varying vec3 vColor;
  varying float vAlpha;
  void main(){
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = clamp(aSize * (uScale / -mv.z), 0.6, 4.0);
    // 아주 느린 반짝임 — 알파만 ±25% 흔들어 '살아 있는' 배경을 만든다
    vColor = aColor;
    vAlpha = aAlpha * (1.0 + 0.25 * sin(uTime * 0.6 + aSeed)) * mix(1.0, ${WARP_ALPHA.toFixed(2)}, uWarp);
    gl_Position = projectionMatrix * mv;
  }
`;
const DUST_FRAGMENT = /* glsl */ `
  uniform sampler2D uMap;
  varying vec3 vColor;
  varying float vAlpha;
  void main(){
    float a = texture2D(uMap, gl_PointCoord).a * vAlpha;
    if (a < 0.004) discard;
    gl_FragColor = vec4(vColor, a);
  }
`;
