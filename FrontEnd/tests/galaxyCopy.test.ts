import { test } from "node:test";
import assert from "node:assert/strict";
import { HELP_SECTIONS, TOUR_STEPS } from "../src/lib/guide.ts";

const text = [...TOUR_STEPS.beam.body, ...HELP_SECTIONS.flatMap((s) => s.body)].join("\n");

test("galaxy help explains the grey co-mention lines", () => {
  assert.match(text, /함께 언급/);
  assert.match(text, /관계의 근거는 아님/);
});

test("help no longer counts four relation buttons and lists the co-mention role", () => {
  assert.doesNotMatch(text, /관계 종류 버튼 4개/);
  assert.match(text, /역할\([^)]*함께 언급[^)]*\)/);
});
