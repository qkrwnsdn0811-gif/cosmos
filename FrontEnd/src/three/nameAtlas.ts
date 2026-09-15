import * as THREE from "three";
import { FONT } from "./logoAtlas";

/**
 * 이름 아틀라스 — 기업명(한글)을 행 단위로 좌→우 패킹한 큰 캔버스 텍스처.
 * 이름표 칩 셰이더가 인스턴스별 셀 사각형(aName = u,v,w,h)으로 자기 이름을 읽어 칩 188개를 드로우콜 1회로 그린다.
 * (troika Text 는 기본 폰트에 한글 글리프가 없어 캔버스로 굽는다)
 */
const SIZE = 2048;
/**
 * 행 높이(px) — 글자 em 36px 에 위아래 4px. 칩은 셀의 세로 전체를 이름 영역(칩 높이의 0.8)에 매핑하므로 행이 높을수록
 * 글자가 그만큼 작아진다: 56 이던 때는 글자가 칩 높이의 0.51 배, 44 로 줄여 0.65 배가 됐다 (칩 26px 에서 글자 ≈ 17px).
 * 실제 잉크+그림자는 한글이 행의 1~41px, 라틴 디센더는 45px 까지라 그림자 꼬리 1px 이 클립에 잘리지만 눈에 띄지 않는다.
 * 행 격리는 여백이 아니라 클립과 "44 가 4 의 배수" 인 점(밉맵 0~2 레벨에서 행 경계가 텍셀에 정렬)이 지킨다 — 4 의 배수가 아닌 값으로 바꾸지 말 것
 */
const ROW = 44;
const PX = 36;
const PAD = 12;
/** 기업명·역할 태그는 보통 굵기(400)로 굽는다 */
const FONT_SPEC = `400 ${PX}px ${FONT}`;

export interface NameCell {
  u: number;
  v: number;
  w: number;
  h: number;
  /** 셀 폭 / 셀 높이 — 칩에서 이름 영역의 가로 비율을 정한다 */
  aspect: number;
}

interface Slot {
  text: string;
  x: number;
  y: number;
  w: number;
}

let canvas: HTMLCanvasElement | null = null;
let ctx: CanvasRenderingContext2D | null = null;
let texture: THREE.CanvasTexture | null = null;
const cells = new Map<string, NameCell | null>();
const slots: Slot[] = [];
let cursorX = 0;
let cursorY = 0;
let fontsHooked = false;

export function nameAtlasTexture() {
  if (texture) return texture;
  canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  ctx = canvas.getContext("2d")!;
  texture = new THREE.CanvasTexture(canvas);
  texture.flipY = false; // 캔버스 좌표(y 아래)를 그대로 uv 로 쓴다
  texture.colorSpace = THREE.SRGBColorSpace;
  // 은하 기본 거리에서는 36px 로 구운 글자가 화면 ~17px 로 축소되어 읽힌다. 밉맵 없이 bilinear 만 쓰면 회전 중 획이 반짝이므로
  // 밉맵을 켠다 (2048² 는 2의 제곱이라 생성이 싸고, 기본 거리의 LOD 는 ≈ 1 이라 행 경계(ROW 44, 4 의 배수)가 텍셀에 정렬돼 이웃 행이 새지 않는다)
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = 4;
  hookFonts();
  return texture;
}

/** 웹폰트(Pretendard)가 늦게 도착하면 폴백 폰트로 구운 셀을 한 번 다시 그린다 */
function hookFonts() {
  if (fontsHooked || typeof document === "undefined" || !("fonts" in document)) return;
  fontsHooked = true;
  document.fonts.ready.then(() => {
    if (!ctx || !texture) return;
    slots.forEach(drawSlot);
    texture.needsUpdate = true;
  });
}

/** 셀 하나를 그린다. 폰트가 바뀌어 글자가 셀보다 넓어지면 가로로만 눌러 넣는다 */
function drawSlot(s: Slot) {
  const c = ctx!;
  c.save();
  c.clearRect(s.x, s.y, s.w, ROW);
  c.beginPath();
  c.rect(s.x, s.y, s.w, ROW);
  c.clip();
  c.font = FONT_SPEC;
  c.textAlign = "center";
  c.textBaseline = "middle";
  const avail = s.w - PAD * 2;
  const measured = c.measureText(s.text).width;
  const squeeze = measured > avail ? avail / measured : 1;
  c.translate(s.x + s.w / 2, s.y + ROW / 2 + 1);
  c.scale(squeeze, 1);
  // 어두운 우주 위에서도 읽히도록 그림자 패스 + 본문 패스
  c.shadowColor = "rgba(3,6,14,0.9)";
  c.shadowBlur = 4; // 행 여백(4px) 안에서 끝나야 이웃 행으로 번지지 않는다
  c.fillStyle = "rgba(3,6,14,0.85)";
  c.fillText(s.text, 0, 0);
  c.shadowBlur = 0;
  c.fillStyle = "#ffffff";
  c.fillText(s.text, 0, 0);
  c.restore();
}

/**
 * 이름 셀을 확보한다. 같은 텍스트는 같은 셀을 재사용하고, 아틀라스가 가득 차면 null (칩은 로고 원만 그린다).
 */
export function ensureNameCell(text: string): NameCell | null {
  nameAtlasTexture();
  const hit = cells.get(text);
  if (hit !== undefined) return hit;
  const label = text.trim() || "·";
  ctx!.font = FONT_SPEC;
  const w = Math.min(SIZE, Math.ceil(ctx!.measureText(label).width) + PAD * 2);
  if (cursorX + w > SIZE) {
    cursorX = 0;
    cursorY += ROW;
  }
  if (cursorY + ROW > SIZE) {
    cells.set(text, null);
    return null;
  }
  const slot: Slot = { text: label, x: cursorX, y: cursorY, w };
  cursorX += w;
  slots.push(slot);
  drawSlot(slot);
  texture!.needsUpdate = true;
  const cell: NameCell = { u: slot.x / SIZE, v: slot.y / SIZE, w: w / SIZE, h: ROW / SIZE, aspect: w / ROW };
  cells.set(text, cell);
  return cell;
}
