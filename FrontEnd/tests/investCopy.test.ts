import { test } from "node:test";
import assert from "node:assert/strict";
import type { EventSignal, ExpectedMove, Fundamental, MoveBacktestRow } from "../src/api/types.ts";
import {
  accuracyText,
  afterNews,
  countsText,
  fmtPct,
  fmtWon,
  gradesHold,
  metricsText,
  moveBadge,
  moveCheckText,
  moveLine,
  pastLine,
  pathText,
  signalText,
} from "../src/features/invest/investCopy.ts";

const signal = (over: Partial<EventSignal> = {}): EventSignal => ({
  horizon: "SHORT",
  windowDays: 3,
  label: "DOWN",
  holdReason: null,
  caseScope: "POSITION",
  caseCount: 120,
  sameShare: 0.65,
  meanAr: -0.0214,
  downShare: 0.65,
  ...over,
});

test("percent and won formatting", () => {
  assert.equal(fmtPct(0.0123), "+1.2%");
  assert.equal(fmtPct(-0.0214), "−2.1%");
  assert.equal(fmtPct(0.0004), "0.0%");
  assert.equal(fmtPct(0.65, 0, false), "65%");
  assert.equal(fmtPct(null), "-");
  assert.equal(fmtWon(1_259_873_040_024_000), "1,260조");
  assert.equal(fmtWon(8_361_953_667_900), "8.4조");
  assert.equal(fmtWon(613_758_208_500), "6,138억");
});

test("signal text always speaks about the past", () => {
  assert.equal(signalText(signal()), "과거 하락 우세");
  assert.equal(signalText(signal({ label: "UP" })), "과거 상승 우세");
  assert.equal(signalText(signal({ label: "HOLD", holdReason: "사례 부족" })), "판단 보류 · 사례 부족");
  assert.equal(signalText(null), "판단 보류");
});

test("past line names the type, who and the window", () => {
  assert.equal(
    pastLine(signal(), "실적 부진", "공급사"),
    "과거 '실적 부진' 뉴스 뒤 공급사 120건: 78건(65%) 하락, 3거래일 평균 초과수익률 −2.1%",
  );
  assert.match(pastLine(signal({ caseScope: "COMPANY" }), "실적 부진", "공급사")!, /뒤 이 종목 120건/);
  assert.equal(pastLine(signal({ caseCount: 0, downShare: null }), "실적 부진", "공급사"), null);
});

test("feed counts skip zeros", () => {
  assert.equal(countsText({ up: 1, down: 2, hold: 5 }), "하락 우세 2 · 상승 우세 1 · 보류 5");
  assert.equal(countsText({ up: 0, down: 0, hold: 3 }), "보류 3");
});

const fundamental = (over: Partial<Fundamental> = {}): Fundamental => ({
  marketCap: 8_361_953_667_900,
  marketCapRank: 4,
  sizeClass: "LARGE",
  per: 12.34,
  pbr: 1.5,
  dividendYield: 0.012,
  netIncomeTtm: 1,
  equity: 1,
  dividendPerShare: 1,
  closePrice: 1,
  priceDate: null,
  fiscalBasis: null,
  ...over,
});

test("metrics order follows the horizon and marks losses", () => {
  assert.equal(metricsText(fundamental(), "SHORT"), "대형 · 시총 8.4조 · PER 12.3 · PBR 1.50 · 배당 1.2%");
  assert.equal(metricsText(fundamental(), "LONG"), "PER 12.3 · PBR 1.50 · 배당 1.2% · 대형 · 시총 8.4조");
  assert.equal(metricsText(fundamental({ per: null, netIncomeTtm: -5, pbr: null, dividendYield: null }), "SHORT"), "대형 · 시총 8.4조 · 적자");
  assert.equal(metricsText(null, "SHORT"), "");
});

test("accuracy compares with the baseline", () => {
  const row = { horizon: "LONG" as const, newsType: null, newsTypeLabel: null, caseCount: 8787, labeledCount: 2752, hitCount: 1393, hitRate: 0.506, upLabels: 0, downLabels: 0, baseDownShare: 0.415, baselineRate: 0.585 };
  assert.deepEqual(accuracyText(row), { line: "딱지 2,752건 중 50.6% 적중 · 무조건 많은 쪽으로 답할 때 58.5%", beats: false });
  assert.equal(accuracyText({ ...row, labeledCount: 0, hitRate: null }), null);
});

test("path text walks outward from the anchor whatever the edge direction", () => {
  const step = (s: string, t: string, rel: string) => ({ sourceCompanyId: s, sourceName: s.toUpperCase(), targetCompanyId: t, targetName: t.toUpperCase(), relationshipType: rel as never, edgeSource: "NEWS", strength: 1, evidence: [] });
  // anchor a; first edge b→a (b supplies a), then b competes with c
  const node = { basis: "INFERRED" as const, companyId: "c", anchorCompanyId: "a", path: [step("b", "a", "SUPPLY"), step("b", "c", "COMPETE")] };
  assert.equal(pathText(node), "A → 공급 → B → 경쟁 → C");
  assert.equal(pathText({ ...node, basis: "REPORTED" }), null);
});

test("evidence published after the news is flagged", () => {
  const dateline = "2026-09-03T07:35:06+09:00";
  assert.equal(afterNews("2026-10-06T09:00:00+09:00", dateline), true);
  assert.equal(afterNews("2026-03-26T09:00:00+09:00", dateline), false);
  assert.equal(afterNews(dateline, dateline), false);
});

const moveRow = (horizon: "SHORT" | "LONG", grade: "SMALL" | "MID" | "LARGE", realizedMove: number): MoveBacktestRow => ({
  horizon,
  windowDays: horizon === "SHORT" ? 3 : 20,
  grade,
  caseCount: 700,
  predictedMove: realizedMove,
  realizedMove,
});

test("move grades count only when the test period grew with them", () => {
  const rows = [
    moveRow("SHORT", "SMALL", 0.039), moveRow("SHORT", "MID", 0.06), moveRow("SHORT", "LARGE", 0.07),
    moveRow("LONG", "SMALL", 0.094), moveRow("LONG", "MID", 0.161), moveRow("LONG", "LARGE", 0.141),
  ];
  assert.equal(gradesHold(rows, "SHORT"), true);
  assert.equal(gradesHold(rows, "LONG"), false);
  assert.equal(gradesHold([], "SHORT"), false);
  assert.deepEqual(moveCheckText(rows, "SHORT"), { line: "'변동 큼' 종목 실제 ±7.0% · '변동 작음' 종목 실제 ±3.9%", holds: true });
  assert.equal(moveCheckText(rows, "LONG")?.holds, false);
  assert.equal(moveCheckText([], "SHORT"), null);
});

test("move badge and line speak about size, not direction", () => {
  const move: ExpectedMove = { horizon: "SHORT", windowDays: 3, expectedMove: 0.0623, caseCount: 312, grade: "LARGE" };
  assert.equal(moveBadge(move, true), "변동 큼 ±6.2%");
  assert.equal(moveBadge(move, false), "변동폭 ±6.2%");
  assert.equal(moveBadge(null, true), null);
  assert.equal(moveLine(move, true), "예상 변동폭: 이 종목은 과거 뉴스 312번 뒤 3거래일 동안 시장 대비 평균 ±6.2% 움직였습니다(오르든 내리든)");
  assert.match(moveLine(move, false) ?? "", /참고치로만/);
});
