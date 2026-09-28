import * as THREE from "three";

/**
 * 장난감 우주 스타일 — 팔레트와 셀 셰이딩 유틸 (아트 디렉션 랩에서 확정, 은하 본편과 공유).
 * 방향: Kurzgesagt 풍 우주. 짙은 네이비 배경, 채도 높은 플랫 컬러, 둥근 저폴리, 2~4단계 셀 셰이딩, 글로우 최소.
 */
// 팔레트·빔 색·행성 반지름 계수는 three 를 모르는 palette.ts 로 옮겼다(HUD 가 three 청크를 끌어오지 않게) — 씬 코드는 여기서 그대로 가져다 쓴다
export { LAB, beamColorFor, PLANET_RADIUS_K } from "./palette";


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


/**
 * detail 별 "정점 → 고유 방향" 표. IcosahedronGeometry 는 인덱스가 없어 같은 꼭짓점이 인접 면마다 반복된다(detail 2: 정점 960개, 고유 162개).
 * 정점 흔들기의 노이즈는 방향(단위 벡터)에만 의존하므로 고유 방향마다 한 번만 재면 된다 — 행성 200개를 만드는 첫 화면에서
 * fbm 호출이 1/3 로 줄어 planetGeometry 전체가 약 100ms → 40ms 가 됐다(2026-09-18 실측). 결과 지오메트리는 이전과 같다.
 * 반지름은 배율일 뿐이라 정점 순서는 radius 와 무관하게 같다(PolyhedronGeometry 가 결정론적으로 만든다)
 */
const uniqueDirCache = new Map<number, { map: Uint16Array; dirs: Float32Array; count: number }>();
function uniqueDirs(detail: number) {
  const hit = uniqueDirCache.get(detail);
  if (hit) return hit;
  const unit = new THREE.IcosahedronGeometry(1, detail);
  const pos = unit.attributes.position as THREE.BufferAttribute;
  const map = new Uint16Array(pos.count);
  const keyOf = new Map<string, number>();
  const dirs: number[] = [];
  for (let i = 0; i < pos.count; i += 1) {
    const x = pos.getX(i);
    const y = pos.getY(i);
    const z = pos.getZ(i);
    // 같은 꼭짓점은 부동소수 오차 안에서 같은 좌표다 — 1e-5 단위로 양자화해 묶는다
    const key = `${Math.round(x * 1e5)},${Math.round(y * 1e5)},${Math.round(z * 1e5)}`;
    let u = keyOf.get(key);
    if (u === undefined) {
      u = dirs.length / 3;
      keyOf.set(key, u);
      dirs.push(x, y, z);
    }
    map[i] = u;
  }
  unit.dispose();
  const out = { map, dirs: new Float32Array(dirs), count: dirs.length / 3 };
  uniqueDirCache.set(detail, out);
  return out;
}

export function planetGeometry(radius: number, palette: PlanetPalette, seed: number, detail = 2, landRatio = 0.45) {
  // IcosahedronGeometry 는 이미 인덱스가 없다 — toNonIndexed 를 다시 부르면 three 가 경고를 찍는다
  const base = new THREE.IcosahedronGeometry(radius, detail);
  const geo = base.index ? base.toNonIndexed() : base;
  const pos = geo.attributes.position as THREE.BufferAttribute;
  const uniq = uniqueDirs(detail);
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
  //    고유 방향마다 한 번씩 재고(uniqueDirs), 정점은 자기 방향의 값을 찾아 쓴다
  const bump = new Float32Array(uniq.count);
  if (uniq.map.length === pos.count) {
    for (let u = 0; u < uniq.count; u += 1) {
      dir.set(uniq.dirs[u * 3], uniq.dirs[u * 3 + 1], uniq.dirs[u * 3 + 2]).normalize();
      bump[u] = fbm(dir.x * 3.4 + seed, dir.y * 3.4 + seed * 2, dir.z * 3.4 + seed * 3);
    }
  }
  for (let i = 0; i < pos.count; i += 1) {
    v.fromBufferAttribute(pos, i);
    let n: number;
    if (uniq.map.length === pos.count) n = bump[uniq.map[i]];
    else {
      // 정점 수가 표와 다르면(three 가 분할 규칙을 바꾼 경우) 예전처럼 정점마다 잰다
      dir.copy(v).normalize();
      n = fbm(dir.x * 3.4 + seed, dir.y * 3.4 + seed * 2, dir.z * 3.4 + seed * 3);
    }
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
