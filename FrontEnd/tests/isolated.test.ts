import { test } from "node:test";
import assert from "node:assert/strict";
import { isolatedNodes } from "../src/three/isolated.ts";

const model = {
  centerId: null as string | null,
  nodes: ["삼성전자", "솔브레인", "원익QnC", "케이엔제이"].map((id) => ({ id })),
  edges: [
    { id: "e1", source: "솔브레인", target: "삼성전자" },
    { id: "e2", source: "원익QnC", target: "삼성전자" },
  ],
};

test("planets with no drawn line are isolated", () => {
  assert.deepEqual([...isolatedNodes(model, new Set(["e1", "e2"]))], ["케이엔제이"]);
});

test("a line hidden by a filter no longer keeps its planets", () => {
  // e2 is a co-mention line and the co-mention chip is off
  assert.deepEqual([...isolatedNodes(model, new Set(["e1"]))].sort(), ["원익QnC", "케이엔제이"].sort());
});

test("the center company and kept ids stay even without a line", () => {
  const centered = { ...model, centerId: "케이엔제이" };
  assert.deepEqual([...isolatedNodes(centered, new Set(["e1"]), ["원익QnC", null])], []);
});
