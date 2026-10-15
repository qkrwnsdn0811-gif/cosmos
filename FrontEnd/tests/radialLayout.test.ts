import { test } from "node:test";
import assert from "node:assert/strict";
import {
  INNER_RADIUS,
  labelSide,
  linkTargetId,
  OUTER_RADIUS,
  radialLayout,
  shortName,
  viaCompanyId,
  type LayoutInput,
} from "../src/features/event/radialLayout.ts";

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


const angle = (p: { x: number; y: number }) => Math.atan2(p.y - C, p.x - C);

test("reported nodes with more inferred companies get wider sectors", () => {
  const placed = radialLayout(
    [reported("A"), reported("B"), reported("C"), inferred("x1", "A"), inferred("x2", "A"), inferred("x3", "A")],
    SIZE,
  );
  const b = placed.find((p) => p.id === "B")!;
  // weights A=4, B=1, C=1 → A spans 4/6 of the circle centred on top; B's centre sits at top + 5π/6
  near(angle(b), -Math.PI / 2 + (5 * Math.PI) / 6);
  near(angle(placed[0]), -Math.PI / 2);
});

test("hop-2 nodes link to the intermediate company when it is on the map", () => {
  const onMap = new Set(["A", "X", "Y"]);
  assert.equal(linkTargetId({ basis: "INFERRED", hop: 2, anchorId: "A", viaId: "X" }, onMap), "X");
  assert.equal(linkTargetId({ basis: "INFERRED", hop: 2, anchorId: "A", viaId: "Z" }, onMap), "A");
  assert.equal(linkTargetId({ basis: "INFERRED", hop: 1, anchorId: "A", viaId: null }, onMap), "A");
  assert.equal(linkTargetId({ basis: "REPORTED", hop: 0, anchorId: null, viaId: null }, onMap), null);
});

test("intermediate company is the far end of the first step", () => {
  const out = [{ sourceCompanyId: "A", targetCompanyId: "X" }, { sourceCompanyId: "X", targetCompanyId: "Y" }];
  const back = [{ sourceCompanyId: "X", targetCompanyId: "A" }, { sourceCompanyId: "Y", targetCompanyId: "X" }];
  assert.equal(viaCompanyId(out, "A"), "X");
  assert.equal(viaCompanyId(back, "A"), "X");
  assert.equal(viaCompanyId(out.slice(0, 1), "A"), null);
});

test("labels go outward on the left and right and below in the middle", () => {
  assert.equal(labelSide(C + 0.3 * SIZE, SIZE), "start");
  assert.equal(labelSide(C - 0.3 * SIZE, SIZE), "end");
  assert.equal(labelSide(C + 0.05 * SIZE, SIZE), "middle");
});

test("long names are shortened to ten characters", () => {
  assert.equal(shortName("Advanced Micro Devices"), "Advanced …");
  assert.equal(shortName("Advanced Micro Devices").length, 10);
  assert.equal(shortName("삼성전자"), "삼성전자");
});

test("hop-2 nodes sit right after their intermediate company on the ring", () => {
  const nodes: LayoutInput[] = [
    reported("A"),
    { id: "h1a", basis: "INFERRED", anchorId: "A" },
    { id: "h1b", basis: "INFERRED", anchorId: "A" },
    { id: "h1c", basis: "INFERRED", anchorId: "A" },
    { id: "h2", basis: "INFERRED", anchorId: "A", viaId: "h1a" },
  ];
  const placed = new Map(radialLayout(nodes, SIZE).map((p) => [p.id, p]));
  const a = (id: string) => (angle(placed.get(id)!) + 2 * Math.PI) % (2 * Math.PI);
  const order = ["h1a", "h1b", "h1c", "h2"].sort((x, y) => a(x) - a(y));
  const i = order.indexOf("h1a");
  assert.equal(order[(i + 1) % order.length], "h2", `ring order ${order.join(",")}`);
});
