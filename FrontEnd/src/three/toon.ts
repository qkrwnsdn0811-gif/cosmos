import * as THREE from "three";
import type { ImpactDirection } from "@/api/types";

/**
 * 장난감 우주 스타일 — 팔레트와 셀 셰이딩 유틸 (아트 디렉션 랩에서 확정, 은하 본편과 공유).
 * 방향: Kurzgesagt 풍 우주. 짙은 네이비 배경, 채도 높은 플랫 컬러, 둥근 저폴리, 2~4단계 셀 셰이딩, 글로우 최소.
 */
export const LAB = {
  bg: "#0b1030",
  bgTop: "#1a2160",
  nebulaA: "#5b3fb8",
  nebulaB: "#2a5fd8",
  star: "#ffb74a",
  starHot: "#ff7a45",
  beam: "#ffa24c",
  beamGlow: "#ff7a45",
  /**
   * 영향 방향별 관계 빔 색 — 긍정 파랑·부정 빨강·중립 보라 (랩의 단색 beam 과 별개로 은하 본편이 쓴다).
   * 톤매핑이 없어 셰이더가 쓴 값이 그대로 화면 sRGB 가 되므로 셋 다 채도를 높게 유지하고,
   * 특히 파랑은 네이비 배경(#0b1030)에 묻히지 않도록 명도를 충분히 올려 잡았다.
   */
  beamPositive: "#4f8dff",
  beamNegative: "#ff4d6d",
  beamNeutral: "#a06bff",
  accent: "#6d8cff",
  planets: {
    teal: { name: "틸 — 대륙형", ocean: "#2fb8c9", oceanDeep: "#2394ad", land: "#7be495", landHi: "#b6f2c0", ring: "#bdedff" },
    violet: { name: "바이올렛 — 고리형", ocean: "#6e55e8", oceanDeep: "#5642c4", land: "#c48bff", landHi: "#e2c4ff", ring: "#e6d3ff" },
    blue: { name: "블루 — 위성형", ocean: "#3d7bff", oceanDeep: "#2f62d6", land: "#9dc4ff", landHi: "#d7e6ff", ring: "#d7e6ff" },
  },
} as const;

/**
 * 영향 방향 → 관계 빔 색. 빔·화살촉·경로 스트립 글리프가 모두 이 한 곳을 보게 해서 색 규칙이 갈라지지 않게 한다.
 * 정의되지 않은 방향이나 값 누락은 중립 보라로 떨어뜨린다 — 색이 비거나 긍정·부정으로 오독되면 안 된다.
 */
export function beamColorFor(impact: ImpactDirection | null | undefined): string {
  if (impact === "POSITIVE") return LAB.beamPositive;
  if (impact === "NEGATIVE") return LAB.beamNegative;
  return LAB.beamNeutral;
}

export interface PlanetPalette {
  name: string;
  ocean: string;
  oceanDeep: string;
  land: string;
  landHi: string;
  ring: string;
}

const gradientCache = new Map<number, THREE.DataTexture>();

/** n 단계 셀 셰이딩용 그라디언트 맵 (MeshToonMaterial.gradientMap) */
export function toonGradient(steps: number) {
  const hit = gradientCache.get(steps);
  if (hit) return hit;
  const data = new Uint8Array(steps * 4);
  for (let i = 0; i < steps; i += 1) {
    // 가장 어두운 단계도 너무 검지 않게 (플랫 컬러 느낌 유지)
    const v = Math.round(255 * (0.38 + (0.62 * i) / Math.max(1, steps - 1)));
    data[i * 4] = v;
    data[i * 4 + 1] = v;
    data[i * 4 + 2] = v;
    data[i * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, steps, 1, THREE.RGBAFormat);
  tex.minFilter = THREE.NearestFilter;
  tex.magFilter = THREE.NearestFilter;
  tex.generateMipmaps = false;
  tex.needsUpdate = true;
  gradientCache.set(steps, tex);
  return tex;
}

/* ------------------------------ 결정론적 노이즈 ------------------------------ */
function hash3(x: number, y: number, z: number) {
  let h = Math.sin(x * 127.1 + y * 311.7 + z * 74.7) * 43758.5453;
  h -= Math.floor(h);
  return h;
}
function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}
function smooth(t: number) {
  return t * t * (3 - 2 * t);
}
function valueNoise(x: number, y: number, z: number) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const zi = Math.floor(z);
  const tx = smooth(x - xi);
  const ty = smooth(y - yi);
  const tz = smooth(z - zi);
  const c = (dx: number, dy: number, dz: number) => hash3(xi + dx, yi + dy, zi + dz);
  const x00 = lerp(c(0, 0, 0), c(1, 0, 0), tx);
  const x10 = lerp(c(0, 1, 0), c(1, 1, 0), tx);
  const x01 = lerp(c(0, 0, 1), c(1, 0, 1), tx);
  const x11 = lerp(c(0, 1, 1), c(1, 1, 1), tx);
  return lerp(lerp(x00, x10, ty), lerp(x01, x11, ty), tz);
}
/** 3옥타브 fbm, 0~1 */
export function fbm(x: number, y: number, z: number) {
  let v = 0;
  let amp = 0.5;
  let f = 1;
  for (let o = 0; o < 3; o += 1) {
    v += valueNoise(x * f, y * f, z * f) * amp;
    f *= 2.1;
    amp *= 0.5;
  }
  return v / 0.875;
}

/**
 * 저폴리 행성 지오메트리 — 면 단위로 바다/대륙 색을 칠하고, 정점을 살짝 흔들어 손으로 깎은 느낌을 낸다.
 * 인덱스를 풀어 면마다 다른 색을 줄 수 있게 하고, 흔들림은 위치 기반이라 이음새가 생기지 않는다.
 */
export function planetGeometry(radius: number, palette: PlanetPalette, seed: number, detail = 2, landRatio = 0.45) {
  // IcosahedronGeometry 는 이미 인덱스가 없다 — toNonIndexed 를 다시 부르면 three 가 경고를 찍는다
  const base = new THREE.IcosahedronGeometry(radius, detail);
  const geo = base.index ? base.toNonIndexed() : base;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const colors = new Float32Array(pos.count * 3);
  const ocean = new THREE.Color(palette.ocean);
  const oceanDeep = new THREE.Color(palette.oceanDeep);
  const land = new THREE.Color(palette.land);
  const landHi = new THREE.Color(palette.landHi);
  const v = new THREE.Vector3();
  const dir = new THREE.Vector3();
  const c = new THREE.Vector3();
  const threshold = 1 - landRatio;

  // 1) 정점 흔들기 — 방향(단위 벡터) 기준으로 노이즈를 읽어 반지름과 무관하게 같은 요철이 나오게 한다
  //    (같은 기업이 은하 뷰와 기업 중심 뷰에서 size 가 달라도 같은 행성으로 보여야 한다). 주파수 3.4 는 반지름 2 짜리 행성의
  //    예전 값(좌표 × 1.7)과 같은 굴곡이다. 위치 기반이라 이음새는 생기지 않는다
  for (let i = 0; i < pos.count; i += 1) {
    v.fromBufferAttribute(pos, i);
    dir.copy(v).normalize();
    const n = fbm(dir.x * 3.4 + seed, dir.y * 3.4 + seed * 2, dir.z * 3.4 + seed * 3);
    v.multiplyScalar(1 + (n - 0.5) * 0.06);
    pos.setXYZ(i, v.x, v.y, v.z);
  }
  // 2) 면 색
  for (let f = 0; f < pos.count; f += 3) {
    c.set(0, 0, 0);
    for (let k = 0; k < 3; k += 1) c.add(v.fromBufferAttribute(pos, f + k));
    c.multiplyScalar(1 / 3).normalize();
    const n = fbm(c.x * 2.2 + seed * 7, c.y * 2.2 + seed * 11, c.z * 2.2 + seed * 13);
    const isLand = n > threshold;
    const col = isLand ? (n > threshold + 0.12 ? landHi : land) : n < threshold - 0.16 ? oceanDeep : ocean;
    for (let k = 0; k < 3; k += 1) {
      colors[(f + k) * 3] = col.r;
      colors[(f + k) * 3 + 1] = col.g;
      colors[(f + k) * 3 + 2] = col.b;
    }
  }
  geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geo.computeVertexNormals();
  return geo;
}

/** 부드러운 원형 글로우 스프라이트 텍스처 */
let glowTex: THREE.CanvasTexture | null = null;
export function glowTexture() {
  if (glowTex) return glowTex;
  const s = 256;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const x = c.getContext("2d")!;
  const g = x.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.45)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  x.fillStyle = g;
  x.fillRect(0, 0, s, s);
  glowTex = new THREE.CanvasTexture(c);
  glowTex.colorSpace = THREE.SRGBColorSpace;
  return glowTex;
}

/** 로고 이름표 텍스처 — 흰 원판 위 로고, 얇은 테두리. 로고는 비동기 로드 후 갱신 */
export function logoTagTexture(url: string | null, monogram: string, border: string) {
  const s = 256;
  const c = document.createElement("canvas");
  c.width = c.height = s;
  const x = c.getContext("2d")!;
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  const draw = (img?: HTMLImageElement) => {
    x.clearRect(0, 0, s, s);
    x.beginPath();
    x.arc(s / 2, s / 2, s / 2 - 6, 0, Math.PI * 2);
    x.fillStyle = "#ffffff";
    x.fill();
    x.lineWidth = 10;
    x.strokeStyle = border;
    x.stroke();
    x.save();
    x.beginPath();
    x.arc(s / 2, s / 2, s / 2 - 16, 0, Math.PI * 2);
    x.clip();
    if (img) {
      const box = s * 0.62;
      const ratio = Math.min(box / img.width, box / img.height);
      const w = img.width * ratio;
      const h = img.height * ratio;
      x.drawImage(img, (s - w) / 2, (s - h) / 2, w, h);
    } else {
      x.fillStyle = border;
      x.font = '800 96px "Pretendard Variable", Pretendard, sans-serif';
      x.textAlign = "center";
      x.textBaseline = "middle";
      x.fillText(monogram, s / 2, s / 2 + 6);
    }
    x.restore();
    tex.needsUpdate = true;
  };
  draw();
  if (url) {
    const img = new Image();
    img.onload = () => draw(img);
    img.src = url;
  }
  return tex;
}

/* ------------------------------ 산업색 → 행성 팔레트 ------------------------------ */
const paletteCache = new Map<string, PlanetPalette>();
const _hsl = { h: 0, s: 0, l: 0 };
function hsl(h: number, s: number, l: number) {
  return `#${new THREE.Color().setHSL(((h % 1) + 1) % 1, THREE.MathUtils.clamp(s, 0, 1), THREE.MathUtils.clamp(l, 0, 1)).getHexString()}`;
}
/** 산업색 하나에서 바다 2톤·대륙 2톤·고리색을 만든다 — 성단마다 색은 다르지만 같은 규칙이라 한 세트로 읽힌다 */
export function planetPaletteFor(industryColor: string): PlanetPalette {
  const hit = paletteCache.get(industryColor);
  if (hit) return hit;
  new THREE.Color(industryColor).getHSL(_hsl);
  const { h, s } = _hsl;
  const l = THREE.MathUtils.clamp(_hsl.l, 0.44, 0.56);
  const p: PlanetPalette = {
    name: industryColor,
    ocean: hsl(h, Math.min(1, s * 1.05), l),
    oceanDeep: hsl(h, s, l * 0.78),
    land: hsl(h + 0.11, s * 0.8, Math.min(0.8, l + 0.24)),
    landHi: hsl(h + 0.13, s * 0.65, Math.min(0.9, l + 0.36)),
    ring: hsl(h, s * 0.5, 0.86),
  };
  paletteCache.set(industryColor, p);
  return p;
}
