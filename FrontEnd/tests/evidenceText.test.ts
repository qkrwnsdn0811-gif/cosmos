import { test } from "node:test";
import assert from "node:assert/strict";
import { evidenceCopyText, evidenceText } from "../src/features/galaxy/evidenceText.ts";
import { EVIDENCE_UI } from "../src/lib/guide.ts";

const base = {
  title: "정부 믿고 삼전닉스 샀는데… ‘머니무브’ 씁쓸한 성적표",
  summary: "“정부 부양책을 믿고 상가를 급매해 주식에 투자했는데, 후회스럽습니다.”\n \n정모씨(75)는 올해 3월 노후 자금줄로 남겨뒀던 서울의 한 지하상가를 4억7000만원에 처분했다. 당시 최고 호가인 6억원보다 훨씬 낮은 가격이었지만",
  publisher: "국민일보",
  publishedAt: "2026-10-13T17:04:00Z",
  originalUrl: "https://example.com/a",
};

test("a news evidence with a sentence is quoted as it is", () => {
  assert.deepEqual(evidenceText({ ...base, evidenceSentence: " 디아이는 삼성전자에 장비를 공급한다. " }),
    { kind: "sentence", text: "디아이는 삼성전자에 장비를 공급한다." });
});

test("a co-mention evidence without a sentence shows the article title and a short preview", () => {
  const t = evidenceText({ ...base, evidenceSentence: null });
  assert.equal(t.kind, "article");
  if (t.kind !== "article") return;
  assert.equal(t.title, base.title);
  assert.ok(t.preview && t.preview.length <= 121 && t.preview.endsWith("…"));
  assert.doesNotMatch(t.preview, /\n/);
});

test("an article without a summary has no preview", () => {
  assert.deepEqual(evidenceText({ ...base, summary: null, evidenceSentence: "" }),
    { kind: "article", title: base.title, preview: null });
});

test("copying a co-mention evidence copies the title, never empty quotes", () => {
  const text = evidenceCopyText({ ...base, evidenceSentence: null });
  assert.ok(text.startsWith(base.title));
  assert.doesNotMatch(text, /""/);
  assert.equal(evidenceCopyText({ ...base, evidenceSentence: "문장." }),
    "\"문장.\" — 국민일보, 2026-10-13, https://example.com/a");
});

test("evidence copy explains co-mention articles, the window behind the count, and has no contribution score", () => {
  assert.match(EVIDENCE_UI.comentionNote, /같은 문장/);
  assert.match(EVIDENCE_UI.timelineSummary(10, "4일 전"), /기간과 관계없이/);
  assert.equal("contribution" in EVIDENCE_UI, false);
});
