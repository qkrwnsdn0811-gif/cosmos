import { test } from "node:test";
import assert from "node:assert/strict";
import { fitDistance } from "../src/three/fit.ts";

test("the whole orbit fits with room to spare on a wide canvas", () => {
  const d = fitDistance(60, 0, 48, 1.6);
  const halfWidth = d * Math.tan(Math.atan(Math.tan((24 * Math.PI) / 180) * 1.6));
  assert.ok(halfWidth >= 60 * 1.5, `half width ${halfWidth}`);
  assert.ok(d >= 60 * 2.4, `distance ${d} should start well outside the orbit`);
});

test("a narrow canvas backs the camera off further", () => {
  assert.ok(fitDistance(60, 0, 48, 0.8) > fitDistance(60, 0, 48, 1.6));
});

test("planets lifted or sunk by the stock reaction are kept in view", () => {
  assert.ok(fitDistance(60, 40, 48, 1.6) > fitDistance(60, 0, 48, 1.6));
});
