import { test } from "node:test";
import assert from "node:assert/strict";
import { strongestPerPair, isComention } from "../src/lib/edgePick.ts";

const e = (s: string, t: string, type: string, score: number) => ({ sourceCompanyId: s, targetCompanyId: t, relationshipType: type, score });

test("a real relation beats a higher-scoring co-mention on the same pair", () => {
  const out = strongestPerPair([e("A", "B", "CO_MENTIONED", 60), e("B", "A", "PARTNER", 61), e("A", "B", "SUPPLY", 70)]);
  assert.deepEqual(out.map((x) => x.relationshipType), ["SUPPLY"]);
  assert.deepEqual(strongestPerPair([e("A", "B", "CO_MENTIONED", 90), e("B", "A", "PARTNER", 61)]).map((x) => x.relationshipType), ["PARTNER"]);
});

test("co-mention stays when it is the only edge, highest score wins", () => {
  const out = strongestPerPair([e("A", "B", "CO_MENTIONED", 20), e("B", "A", "CO_MENTIONED", 40), e("C", "D", "COMPETE", 50)]);
  assert.deepEqual(out.map((x) => [x.relationshipType, x.score]), [["CO_MENTIONED", 40], ["COMPETE", 50]]);
});

test("isComention", () => {
  assert.equal(isComention("CO_MENTIONED"), true);
  assert.equal(isComention("SUPPLY"), false);
});
