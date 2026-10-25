import { test } from "node:test";
import assert from "node:assert/strict";
import { isolatedNodes, shownCount } from "../src/three/isolated.ts";

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

test("the shown count follows the relation-type chips and the minimum score", () => {
  const scored = {
    ...model,
    edges: [
      { id: "e1", source: "솔브레인", target: "삼성전자", type: "SUPPLY", score: 80 },
      { id: "e2", source: "원익QnC", target: "삼성전자", type: "CO_MENTIONED", score: 40 },
    ],
  };
  assert.equal(shownCount(scored, 0, new Set(["SUPPLY", "CO_MENTIONED"])), 3);
  assert.equal(shownCount(scored, 0, new Set(["SUPPLY"])), 2);
  assert.equal(shownCount(scored, 90, new Set(["SUPPLY", "CO_MENTIONED"])), 0);
});
