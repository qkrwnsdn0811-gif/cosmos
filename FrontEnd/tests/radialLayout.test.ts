import { test } from "node:test";
import assert from "node:assert/strict";
import { INNER_RADIUS, OUTER_RADIUS, radialLayout, type LayoutInput } from "../src/features/event/radialLayout.ts";

const SIZE = 500;
const C = SIZE / 2;
const reported = (id: string): LayoutInput => ({ id, basis: "REPORTED", anchorId: null });
const inferred = (id: string, anchorId: string | null): LayoutInput => ({ id, basis: "INFERRED", anchorId });
const dist = (p: { x: number; y: number }) => Math.hypot(p.x - C, p.y - C);
const near = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-6, `${a} ≈ ${b}`);

test("single reported node sits at the top of the inner ring", () => {
  const [p] = radialLayout([reported("A")], SIZE);
  near(p.x, C);
  near(p.y, C - INNER_RADIUS * SIZE);
  assert.equal(p.ring, 1);
});

test("two reported nodes are opposite", () => {
  const [a, b] = radialLayout([reported("A"), reported("B")], SIZE);
  assert.ok(a.y < C && b.y > C);
  near(a.x, C);
  near(b.x, C);
});

test("inferred node stays in its anchor's sector", () => {
  const placed = radialLayout([reported("A"), reported("B"), inferred("X", "A")], SIZE);
  const x = placed.find((p) => p.id === "X")!;
  assert.ok(x.y < C, "anchored to A (top), so it must be in the upper half");
  assert.equal(x.ring, 2);
});

test("inferred nodes lie on the outer ring", () => {
  const placed = radialLayout([reported("A"), inferred("X", "A"), inferred("Y", null)], SIZE);
  for (const p of placed.filter((p) => p.ring === 2)) near(dist(p), OUTER_RADIUS * SIZE);
});

test("eight reported nodes get distinct angles", () => {
  const placed = radialLayout(["a", "b", "c", "d", "e", "f", "g", "h"].map(reported), SIZE);
  const keys = new Set(placed.map((p) => `${p.x.toFixed(3)},${p.y.toFixed(3)}`));
  assert.equal(keys.size, 8);
});

test("no inferred and empty input", () => {
  assert.deepEqual(radialLayout([], SIZE), []);
  const placed = radialLayout([reported("A"), reported("B")], SIZE);
  assert.ok(placed.every((p) => p.ring === 1));
  assert.deepEqual(placed.map((p) => p.id), ["A", "B"]);
});
