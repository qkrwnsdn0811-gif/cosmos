import * as THREE from "three";

/**
 * 로고 아틀라스 — 기업 로고(또는 모노그램)를 셀 하나씩 그린 큰 캔버스 텍스처.
 * 로고 디스크 셰이더가 인스턴스별 셀 원점(aCell)으로 자기 로고를 읽는다.
 * 로고 이미지는 비동기로 로드되어 해당 셀만 다시 그리고 텍스처를 갱신한다.
 */
const CELL = 128;
const COLS = 16; // 16 x 16 = 256 기업
const SIZE = CELL * COLS;
/** 한글 글리프가 있는 폰트 스택 — 로고 모노그램과 이름 아틀라스가 공유한다 */
export const FONT = 'Pretendard Variable, Pretendard, "Malgun Gothic", "Apple SD Gothic Neo", "Segoe UI", sans-serif';

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let texture: THREE.CanvasTexture | null = null;
const cells = new Map<string, number>();

export const ATLAS_CELL_UV = 1 / COLS;

export function logoAtlasTexture() {
  if (texture) return texture;
  canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  ctx = canvas.getContext("2d")!;
  texture = new THREE.CanvasTexture(canvas);
  texture.flipY = false; // 캔버스 좌표(y 아래)를 그대로 uv 로 쓴다 — 셀 행이 뒤집히지 않도록
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = false;
  texture.anisotropy = 4;
  return texture;
}

function cellOrigin(index: number) {
  return { x: (index % COLS) * CELL, y: Math.floor(index / COLS) * CELL };
}

/** 이미지 가장자리 색 표본 (투명이면 null) — 로고 주변 여백을 로고 배경색으로 채우기 위해 */
function edgeColor(img: HTMLImageElement) {
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  const x = c.getContext("2d")!;
  x.drawImage(img, 0, 0, 16, 16);
  const d = x.getImageData(0, 0, 16, 16).data;
  let r = 0;
  let g = 0;
  let b = 0;
  let cnt = 0;
  let transparent = 0;
  const idxs: number[] = [];
  for (let i = 0; i < 16; i += 1) idxs.push(i, 15 * 16 + i, i * 16, i * 16 + 15);
  idxs.forEach((i) => {
    const o = i * 4;
    if (d[o + 3] < 40) transparent += 1;
    else {
      r += d[o];
      g += d[o + 1];
      b += d[o + 2];
      cnt += 1;
    }
  });
  if (transparent > idxs.length * 0.5 || cnt === 0) return null;
  return `rgb(${Math.round(r / cnt)},${Math.round(g / cnt)},${Math.round(b / cnt)})`;
}

function clipCell(index: number) {
  const { x, y } = cellOrigin(index);
  ctx!.save();
  ctx!.clearRect(x, y, CELL, CELL);
  ctx!.beginPath();
  ctx!.arc(x + CELL / 2, y + CELL / 2, CELL / 2 - 1, 0, Math.PI * 2);
  ctx!.clip();
  return { x, y };
}

/**
 * 로고 셀을 확보하고 인덱스를 돌려준다. 같은 key 는 같은 셀을 재사용한다.
 * 반환 셀의 uv 원점은 (index % 16, floor(index / 16)) * ATLAS_CELL_UV.
 */
export function ensureLogoCell(key: string, monogram: string, color: string, logoUrl: string | null): number {
  logoAtlasTexture();
  const hit = cells.get(key);
  if (hit !== undefined) return hit;
  const index = cells.size;
  if (index >= COLS * COLS) return -1;
  cells.set(key, index);

  // 1) 모노그램을 먼저 그린다 (로고가 없거나 로드 전)
  {
    const { x, y } = clipCell(index);
    const g = ctx!.createRadialGradient(x + CELL * 0.4, y + CELL * 0.36, CELL * 0.08, x + CELL / 2, y + CELL / 2, CELL * 0.55);
    g.addColorStop(0, "#f6f7fb");
    g.addColorStop(1, "#d9dde8");
    ctx!.fillStyle = g;
    ctx!.fillRect(x, y, CELL, CELL);
    ctx!.fillStyle = color;
    ctx!.textAlign = "center";
    ctx!.textBaseline = "middle";
    ctx!.font = `800 ${monogram.length > 1 ? 50 : 62}px ${FONT}`;
    ctx!.fillText(monogram, x + CELL / 2, y + CELL / 2 + 3);
    ctx!.restore();
    texture!.needsUpdate = true;
  }

  // 2) 로고 이미지가 있으면 로드 후 셀을 덮어 그린다
  if (logoUrl) {
    const img = new Image();
    img.onload = () => {
      const { x, y } = clipCell(index);
      ctx!.fillStyle = edgeColor(img) ?? "#ffffff";
      ctx!.fillRect(x, y, CELL, CELL);
      const box = CELL * 0.9;
      const ratio = Math.min(box / img.width, box / img.height);
      const w = img.width * ratio;
      const h = img.height * ratio;
      ctx!.drawImage(img, x + (CELL - w) / 2, y + (CELL - h) / 2, w, h);
      ctx!.restore();
      texture!.needsUpdate = true;
    };
    img.src = logoUrl;
  }
  return index;
}

export function cellUv(index: number): [number, number] {
  if (index < 0) return [0, 0];
  return [(index % COLS) * ATLAS_CELL_UV, Math.floor(index / COLS) * ATLAS_CELL_UV];
}
