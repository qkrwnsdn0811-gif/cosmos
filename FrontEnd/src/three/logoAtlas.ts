import * as THREE from "three";

/**
 * 로고 아틀라스 — 기업 로고(또는 모노그램)를 셀 하나씩 그린 큰 캔버스 텍스처.
 * 로고 디스크 셰이더가 인스턴스별 셀 원점(aCell)으로 자기 로고를 읽는다.
 * 로고 이미지는 비동기로 로드되어 해당 셀만 다시 그리고, GPU 반영은 묶어서(FLUSH_MS) 바뀐 셀만 부분 업로드한다.
 */
const CELL = 128;
const COLS = 16; // 16 x 16 = 256 기업
const SIZE = CELL * COLS;
/** 한글 글리프가 있는 폰트 스택 — 로고 모노그램과 이름 아틀라스가 공유한다 */
export const FONT = 'Pretendard Variable, Pretendard, "Malgun Gothic", "Apple SD Gothic Neo", "Segoe UI", sans-serif';
/**
 * 로고 도착 → GPU 반영 병합 간격(ms). 예전에는 이미지 onload 마다 needsUpdate 를 세워 2048² 캔버스 전체(≈16MB)를 다시 올렸고,
 * 로고 200개가 도착하는 1~3초 동안 거의 매 프레임 그 업로드가 돌아 인트로 비행이 100ms 대 프레임으로 끊겼다(2026-09-18 실측).
 * 이제는 이 간격마다 한 번, 바뀐 셀(128²·64KB)만 texSubImage2D 로 올린다 — 렌더러를 모르는 경우(미니 씬만 있을 때)에만 전체 업로드로 떨어진다.
 */
const FLUSH_MS = 250;

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let texture: THREE.CanvasTexture | null = null;
const cells = new Map<string, number>();
/** GPU 에 아직 반영하지 않은 셀 인덱스 */
const dirtyCells = new Set<number>();
let flushTimer = 0;
/** 부분 업로드(copyTextureToTexture)에 쓸 메인 씬 렌더러 — Scene.tsx 가 Canvas 생성 시 묶고 떠날 때 푼다 */
let renderer: THREE.WebGLRenderer | null = null;
/** 셀 하나 크기의 스크래치 캔버스·텍스처 — 부분 업로드의 원본. copyTextureToTexture 는 srcTexture.image 를 그대로 texSubImage2D 에 넘긴다 */
let cellCanvas: HTMLCanvasElement | null = null;
let cellCtx: CanvasRenderingContext2D | null = null;
let cellTexture: THREE.CanvasTexture | null = null;
const DST = new THREE.Vector2();

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

/**
 * 부분 업로드에 쓸 렌더러를 묶는다(null 이면 푼다). 메인 씬(Scene.tsx)만 묶는다 — 뉴스 탭의 미니 씬은 자기 컨텍스트에 캔버스 전체를
 * 새로 올리므로 그쪽에서는 전체 업로드(needsUpdate)로 충분하고, 두 씬은 라우트가 달라 동시에 살지 않는다.
 * 캔버스가 항상 원본이라 어느 렌더러가 나중에 텍스처를 만들어도 그때까지 도착한 로고가 다 들어 있다.
 */
export function bindLogoAtlasRenderer(r: THREE.WebGLRenderer | null) {
  renderer = r;
}

/**
 * 디코딩이 끝난 로고를 셀에 그리는 일은 프레임마다 DRAW_PER_TICK 개씩만 한다. 로고 200개의 fetch 는 거의 동시에 끝나 콜백이 한 프레임에
 * 몰리는데(실측 140ms 안팎), 그리기(클립·fillRect·drawImage·가장자리 표본)를 나눠 하면 프레임당 몇 ms 로 흩어진다. GPU 반영은 어차피 markDirty 가 묶는다
 */
const DRAW_PER_TICK = 24;
const drawQueue: { index: number; img: LogoImage }[] = [];
let drawTimer = 0;

function drainDraws() {
  drawTimer = 0;
  if (!ctx) return;
  const batch = drawQueue.splice(0, DRAW_PER_TICK);
  batch.forEach(({ index, img }) => {
    const { x, y } = clipCell(index);
    ctx!.fillStyle = edgeColor(img) ?? "#ffffff";
    ctx!.fillRect(x, y, CELL, CELL);
    const box = CELL * 0.9;
    const ratio = Math.min(box / img.width, box / img.height);
    const w = img.width * ratio;
    const h = img.height * ratio;
    ctx!.drawImage(img, x + (CELL - w) / 2, y + (CELL - h) / 2, w, h);
    ctx!.restore();
    if ("close" in img) img.close();
    markDirty(index);
  });
  if (drawQueue.length) drawTimer = requestAnimationFrame(drainDraws);
}

function enqueueDraw(index: number, img: LogoImage) {
  drawQueue.push({ index, img });
  if (!drawTimer) drawTimer = requestAnimationFrame(drainDraws);
}

/** 셀이 바뀌었다고 표시만 해 두고, GPU 반영은 FLUSH_MS 뒤 한 번에 한다 */
function markDirty(index: number) {
  dirtyCells.add(index);
  if (flushTimer) return;
  flushTimer = window.setTimeout(flush, FLUSH_MS);
}

function flush() {
  flushTimer = 0;
  if (!texture || !canvas || dirtyCells.size === 0) return;
  // 렌더러가 없거나(미니 씬만), 아직 이 텍스처를 GPU 에 올린 적이 없으면 전체 업로드 — 부분 복사는 이미 만들어진 GPU 텍스처에만 쓴다
  const uploaded = renderer ? Boolean((renderer.properties.get(texture) as { __webglTexture?: unknown }).__webglTexture) : false;
  if (!renderer || !uploaded || texture.version === 0) {
    dirtyCells.clear();
    texture.needsUpdate = true;
    return;
  }
  try {
    if (!cellCanvas || !cellCtx || !cellTexture) {
      cellCanvas = document.createElement("canvas");
      cellCanvas.width = cellCanvas.height = CELL;
      cellCtx = cellCanvas.getContext("2d")!;
      cellTexture = new THREE.CanvasTexture(cellCanvas);
      cellTexture.flipY = false;
      cellTexture.colorSpace = THREE.SRGBColorSpace;
    }
    dirtyCells.forEach((index) => {
      const { x, y } = cellOrigin(index);
      cellCtx!.clearRect(0, 0, CELL, CELL);
      cellCtx!.drawImage(canvas!, x, y, CELL, CELL, 0, 0, CELL, CELL);
      // 원본(srcRegion null) = 스크래치 전체 128², 목적지 = 아틀라스의 셀 원점. flipY 는 둘 다 false 라 행이 맞는다
      renderer!.copyTextureToTexture(cellTexture!, texture!, null, DST.set(x, y));
    });
    dirtyCells.clear();
  } catch {
    // 컨텍스트 손실 등 — 예전 방식(전체 업로드)으로 되돌린다
    dirtyCells.clear();
    texture.needsUpdate = true;
  }
}

function cellOrigin(index: number) {
  return { x: (index % COLS) * CELL, y: Math.floor(index / COLS) * CELL };
}

type LogoImage = ImageBitmap | HTMLImageElement;

/** 가장자리 색 표본용 스크래치 캔버스 — 매 로고마다 만들지 않고 하나를 재사용한다. 읽기 위주라 소프트웨어 캔버스로 두면 getImageData 가 GPU 왕복 없이 빠르다 */
let edgeCanvas: CanvasRenderingContext2D | null = null;
function edgeCtx() {
  if (edgeCanvas) return edgeCanvas;
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  edgeCanvas = c.getContext("2d", { willReadFrequently: true })!;
  return edgeCanvas;
}

/** 이미지 가장자리 색 표본 (투명이면 null) — 로고 주변 여백을 로고 배경색으로 채우기 위해 */
function edgeColor(img: LogoImage) {
  const x = edgeCtx();
  x.clearRect(0, 0, 16, 16);
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
 * 로고 이미지를 받는다. createImageBitmap 은 디코딩을 메인 스레드 밖에서 하므로 — <img> 는 onload 뒤 첫 drawImage 에서
 * 메인 스레드가 디코딩해 로고마다 수십 ms 를 쓴다 — 지원되면 그쪽을 쓰고, 안 되면(또는 실패하면) <img> 로 돌아간다.
 */
async function loadLogo(url: string): Promise<LogoImage | null> {
  if (typeof createImageBitmap === "function") {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      return await createImageBitmap(await res.blob());
    } catch {
      /* 아래 <img> 폴백 */
    }
  }
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
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

  // 1) 모노그램을 먼저 그린다 (로고가 없거나 로드 전). 모델 하나의 셀은 같은 렌더 안에서 한꺼번에 그려지므로 바로 올려도 업로드는 다음 프레임에 한 번이다
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

  // 2) 로고 이미지가 있으면 로드 후 셀을 덮어 그린다 — 그리기는 enqueueDraw 가 프레임마다 나누고, GPU 반영은 markDirty 가 묶는다
  if (logoUrl) {
    void loadLogo(logoUrl).then((img) => {
      if (img) enqueueDraw(index, img);
    });
  }
  return index;
}

export function cellUv(index: number): [number, number] {
  if (index < 0) return [0, 0];
  return [(index % COLS) * ATLAS_CELL_UV, Math.floor(index / COLS) * ATLAS_CELL_UV];
}
