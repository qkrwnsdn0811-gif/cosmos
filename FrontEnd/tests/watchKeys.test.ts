import { test } from "node:test";
import assert from "node:assert/strict";
import { holdsWatchState } from "../src/lib/watchKeys.ts";

test("only queries that carry watched flags refetch after a watch toggle", () => {
  assert.equal(holdsWatchState(["company", "k1", "id"]), true); // 기업 상세 (watched)
  assert.equal(holdsWatchState(["companies", "directory", "k1", {}]), true);
  assert.equal(holdsWatchState(["companies", "search", "k1", "삼성"]), true);
  assert.equal(holdsWatchState(["companies", "quotes"]), false); // 시세판 — 관심과 무관
  assert.equal(holdsWatchState(["companies", "related", "id"]), false);
  assert.equal(holdsWatchState(["companies", "fundamental", "id"]), false);
  assert.equal(holdsWatchState(["company", "prices", "id", "1Y"]), false);
  assert.equal(holdsWatchState(["company", "metrics", "id", "30D"]), false);
  assert.equal(holdsWatchState(["news", "list"]), false);
});
