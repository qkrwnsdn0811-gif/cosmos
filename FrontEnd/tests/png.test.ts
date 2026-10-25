import { test } from "node:test";
import assert from "node:assert/strict";
import { decodePng, encodePng, resizeRgba, shrinkPng } from "../scripts/png.mjs";

/** w×h, 각 픽셀 = f(x, y) 인 RGBA 이미지 */
function image(w: number, h: number, f: (x: number, y: number) => [number, number, number, number]) {
  const data = Buffer.alloc(w * h * 4);
  for (let y = 0; y < h; y += 1) for (let x = 0; x < w; x += 1) data.set(f(x, y), (y * w + x) * 4);
  return { width: w, height: h, data };
}

test("encodePng → decodePng 는 픽셀을 그대로 되돌린다", () => {
  const img = image(5, 3, (x, y) => [x * 50, y * 80, (x + y) * 20, 255 - x * 10]);
  const back = decodePng(encodePng(img));
  assert.equal(back.width, 5);
  assert.equal(back.height, 3);
  assert.deepEqual([...back.data], [...img.data]);
});

test("resizeRgba 는 면적 평균으로 줄이고, 투명 픽셀의 색은 섞지 않는다", () => {
  // 왼쪽 두 열은 빨강, 오른쪽 두 열은 파랑 → 2×1 로 줄이면 각자 제 색
  const half = resizeRgba(image(4, 2, (x) => (x < 2 ? [255, 0, 0, 255] : [0, 0, 255, 255])), 2, 1);
  assert.deepEqual([...half.data], [255, 0, 0, 255, 0, 0, 255, 255]);
  // 흰 불투명 + 검은 완전 투명 → 색은 흰색 그대로, 알파만 절반
  const edge = resizeRgba(image(2, 1, (x) => (x === 0 ? [255, 255, 255, 255] : [0, 0, 0, 0])), 1, 1);
  assert.deepEqual([...edge.data], [255, 255, 255, 128]);
});

test("shrinkPng 는 긴 변을 max 로 맞추고, 이미 작으면 null", () => {
  const big = encodePng(image(480, 240, () => [10, 20, 30, 255]));
  const small = decodePng(shrinkPng(big, 128));
  assert.equal(small.width, 128);
  assert.equal(small.height, 64);
  assert.deepEqual([...small.data.subarray(0, 4)], [10, 20, 30, 255]);
  assert.equal(shrinkPng(encodePng(image(64, 64, () => [0, 0, 0, 255])), 128), null);
  assert.equal(shrinkPng(Buffer.from("not a png"), 128), null);
});
