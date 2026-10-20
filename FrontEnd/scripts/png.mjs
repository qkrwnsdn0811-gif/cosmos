/**
 * PNG 축소 — 의존성 없이 node:zlib 만으로 PNG 를 RGBA 로 풀고, 면적 평균으로 줄여 다시 PNG 로 쓴다.
 * 로고 수집(fetch-logos.mjs)이 128px 보다 큰 그림(토스증권 종목 아이콘은 480~1920px)을 받아 둘 때만 쓴다 —
 * sharp 같은 네이티브 모듈을 개발 의존성에 들이지 않으려고 직접 짰다.
 * 지원: 비트 깊이 1·2·4·8(팔레트·회색) / 8·16(RGB·RGBA·회색+알파), 인터레이스 없음. 그 밖에는 decodePng 가 null 을 돌려준다.
 */
import { deflateSync, inflateSync } from "node:zlib";

const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
/** 색 형식별 채널 수 — 0 회색, 2 RGB, 3 팔레트, 4 회색+알파, 6 RGBA */
const CHANNELS = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 };

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

/** PNG 버퍼 → { width, height, data(RGBA8) }. 못 읽는 형식이면 null */
export function decodePng(buf) {
  if (buf.length < 8 || !buf.subarray(0, 8).equals(SIGNATURE)) return null;
  let width = 0;
  let height = 0;
  let depth = 0;
  let type = -1;
  let palette = null;
  let trns = null;
  const idat = [];
  for (let o = 8; o + 8 <= buf.length; ) {
    const len = buf.readUInt32BE(o);
    const kind = buf.toString("ascii", o + 4, o + 8);
    const body = buf.subarray(o + 8, o + 8 + len);
    if (kind === "IHDR") {
      width = body.readUInt32BE(0);
      height = body.readUInt32BE(4);
      depth = body[8];
      type = body[9];
      if (body[12] !== 0) return null; // 인터레이스(Adam7)는 다루지 않는다
    } else if (kind === "PLTE") palette = body;
    else if (kind === "tRNS") trns = body;
    else if (kind === "IDAT") idat.push(body);
    else if (kind === "IEND") break;
    o += 12 + len;
  }
  const channels = CHANNELS[type];
  if (!width || !height || !channels || ![1, 2, 4, 8, 16].includes(depth)) return null;
  if (type === 3 && !palette) return null;
  let raw;
  try {
    raw = inflateSync(Buffer.concat(idat));
  } catch {
    return null;
  }
  const bitsPerPixel = channels * depth;
  const stride = Math.ceil((width * bitsPerPixel) / 8);
  const bpp = Math.max(1, bitsPerPixel >> 3);
  if (raw.length < (stride + 1) * height) return null;

  // 1) 필터 되돌리기
  const px = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y += 1) {
    const filter = raw[y * (stride + 1)];
    const src = y * (stride + 1) + 1;
    const row = y * stride;
    const prev = row - stride;
    for (let x = 0; x < stride; x += 1) {
      const a = x >= bpp ? px[row + x - bpp] : 0;
      const b = y > 0 ? px[prev + x] : 0;
      const c = x >= bpp && y > 0 ? px[prev + x - bpp] : 0;
      const v = raw[src + x];
      px[row + x] = (filter === 0 ? v : filter === 1 ? v + a : filter === 2 ? v + b : filter === 3 ? v + ((a + b) >> 1) : v + paeth(a, b, c)) & 0xff;
    }
  }

  // 2) 표본 하나 읽기 (16비트는 상위 바이트, 8 미만은 비트 꺼내기)
  const sample = (y, i) => {
    const row = y * stride;
    if (depth === 8) return px[row + i];
    if (depth === 16) return px[row + i * 2];
    const bit = i * depth;
    const v = (px[row + (bit >> 3)] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1);
    return v;
  };
  const scale = depth < 8 && type !== 3 ? 255 / ((1 << depth) - 1) : 1;
  const out = Buffer.alloc(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const o = (y * width + x) * 4;
      const i = x * channels;
      if (type === 3) {
        const p = sample(y, i);
        out[o] = palette[p * 3] ?? 0;
        out[o + 1] = palette[p * 3 + 1] ?? 0;
        out[o + 2] = palette[p * 3 + 2] ?? 0;
        out[o + 3] = trns && p < trns.length ? trns[p] : 255;
      } else if (type === 0 || type === 4) {
        const g = Math.round(sample(y, i) * scale);
        out[o] = out[o + 1] = out[o + 2] = g;
        out[o + 3] = type === 4 ? sample(y, i + 1) : 255;
      } else {
        out[o] = sample(y, i);
        out[o + 1] = sample(y, i + 1);
        out[o + 2] = sample(y, i + 2);
        out[o + 3] = type === 6 ? sample(y, i + 3) : 255;
      }
    }
  }
  return { width, height, data: out };
}

/**
 * 면적 평균 축소 — 원본 픽셀이 목적 픽셀에 겹치는 넓이만큼 섞는다(정수배가 아닌 480 → 128 도 고르게).
 * 알파를 곱한 값으로 섞어 투명한 가장자리의 색이 검게 번지지 않게 한다
 */
export function resizeRgba(img, w, h) {
  const { width: sw, height: sh, data } = img;
  const out = Buffer.alloc(w * h * 4);
  const fx = sw / w;
  const fy = sh / h;
  for (let y = 0; y < h; y += 1) {
    const y0 = y * fy;
    const y1 = y0 + fy;
    for (let x = 0; x < w; x += 1) {
      const x0 = x * fx;
      const x1 = x0 + fx;
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      let area = 0;
      for (let sy = Math.floor(y0); sy < Math.min(sh, Math.ceil(y1)); sy += 1) {
        const wy = Math.min(y1, sy + 1) - Math.max(y0, sy);
        for (let sx = Math.floor(x0); sx < Math.min(sw, Math.ceil(x1)); sx += 1) {
          const wgt = wy * (Math.min(x1, sx + 1) - Math.max(x0, sx));
          const o = (sy * sw + sx) * 4;
          const al = data[o + 3] * wgt;
          r += data[o] * al;
          g += data[o + 1] * al;
          b += data[o + 2] * al;
          a += al;
          area += wgt;
        }
      }
      const o = (y * w + x) * 4;
      if (a > 0) {
        out[o] = Math.round(r / a);
        out[o + 1] = Math.round(g / a);
        out[o + 2] = Math.round(b / a);
      }
      out[o + 3] = Math.round(a / area);
    }
  }
  return { width: w, height: h, data: out };
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(kind, body) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(body.length, 0);
  head.write(kind, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), body])), 0);
  return Buffer.concat([head, body, crc]);
}

/** RGBA8 → PNG. 행마다 Paeth 필터를 걸고 최고 압축으로 묶는다 (로고 128² 한 장이 수~수십 KB) */
export function encodePng(img) {
  const { width, height, data } = img;
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    const dst = y * (stride + 1);
    raw[dst] = 4;
    for (let x = 0; x < stride; x += 1) {
      const i = y * stride + x;
      const a = x >= 4 ? data[i - 4] : 0;
      const b = y > 0 ? data[i - stride] : 0;
      const c = x >= 4 && y > 0 ? data[i - stride - 4] : 0;
      raw[dst + 1 + x] = (data[i] - paeth(a, b, c)) & 0xff;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([SIGNATURE, chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw, { level: 9 })), chunk("IEND", Buffer.alloc(0))]);
}

/** PNG 를 긴 변 max px 이하로 줄인다. 이미 작거나 못 읽는 형식이면 null (호출 측이 원본을 쓸지 정한다) */
export function shrinkPng(buf, max) {
  const img = decodePng(buf);
  if (!img) return null;
  const k = max / Math.max(img.width, img.height);
  if (k >= 1) return null;
  return encodePng(resizeRgba(img, Math.max(1, Math.round(img.width * k)), Math.max(1, Math.round(img.height * k))));
}
