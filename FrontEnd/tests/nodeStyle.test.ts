import { test } from "node:test";
import assert from "node:assert/strict";
import { impactLine, nodeStyle, type NodeStyleInput } from "../src/features/event/nodeStyle.ts";

const base: NodeStyleInput = { basis: "INFERRED", status: "OK", score: 0.5, impactDirection: null, impactMagnitude: null, confidence: null };

test("HOLD wins over stale model values", () => {
  const n = { ...base, status: "HOLD" as const, impactDirection: "POSITIVE" as const, impactMagnitude: 0.9, confidence: 0.8 };
  const s = nodeStyle(n);
  assert.equal(s.fill, "var(--text-4)");
  assert.equal(s.fillOpacity, 0.6);
  assert.equal(s.impactLabel, null);
  assert.equal(impactLine(n), null);
});

test("nodes without the model keep basis colors and the old radius", () => {
  assert.deepEqual(nodeStyle({ ...base, basis: "REPORTED", score: 1 }), { fill: "var(--primary)", fillOpacity: 1, stroke: null, radius: 18, impactLabel: null });
  assert.equal(nodeStyle({ ...base, score: null }).radius, 13);
  assert.equal(nodeStyle(base).fill, "var(--accent)");
});

test("directions get colors and magnitude-based radius", () => {
  const up = nodeStyle({ ...base, impactDirection: "POSITIVE", impactMagnitude: 0.5, confidence: 0.7 });
  assert.equal(up.fill, "var(--pos)");
  assert.equal(up.radius, 15);
  assert.equal(up.impactLabel, "상승 추정");
  assert.equal(nodeStyle({ ...base, impactDirection: "NEGATIVE", impactMagnitude: 0, confidence: 0.7 }).fill, "var(--neg)");
  assert.equal(nodeStyle({ ...base, impactDirection: "NEUTRAL", impactMagnitude: 0, confidence: 0.7 }).fill, "var(--impact-neutral)");
});

test("held direction is a hollow circle with the basis color ring", () => {
  const s = nodeStyle({ ...base, impactMagnitude: 0.25, confidence: 0.41 });
  assert.deepEqual(s, { fill: "var(--panel-solid)", fillOpacity: 1, stroke: "var(--accent)", radius: 12, impactLabel: "방향 보류" });
  assert.equal(nodeStyle({ ...base, basis: "REPORTED", confidence: 0.41 }).stroke, "var(--primary)");
});

test("missing magnitude with a confidence does not produce NaN", () => {
  assert.equal(nodeStyle({ ...base, confidence: 0.41 }).radius, 9);
});

test("panel line", () => {
  assert.equal(impactLine({ ...base, impactDirection: "POSITIVE", impactMagnitude: 0.31, confidence: 0.62 }), "모델 추정: 상승 · 신뢰도 62% · 세기 0.31");
  assert.equal(impactLine({ ...base, impactMagnitude: 0.12, confidence: 0.41 }), "영향 방향 보류 · 신뢰도 41%");
  assert.equal(impactLine(base), null);
});

