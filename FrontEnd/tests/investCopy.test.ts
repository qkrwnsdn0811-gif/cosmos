import { test } from "node:test";
import assert from "node:assert/strict";
import type { EventSignal, ExpectedMove, Fundamental, MoveBacktestRow } from "../src/api/types.ts";
import {
  afterNews,
  countsText,
  fmtPct,
  fmtWon,
  gradesHold,
  josa,
  metricsText,
  moveBadge,
  moveCheckText,
  moveLine,
  pastLine,
  pathText,
  signalText,
  whyText,
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
  // 판단 보류는 피드에 쓰지 않는다 (2026-10-25 사용자 요청)
  assert.equal(countsText({ up: 1, down: 2, hold: 5 }), "하락 우세 2 · 상승 우세 1");
  assert.equal(countsText({ up: 0, down: 0, hold: 3 }), "");
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

test("path text walks outward from the anchor and draws each relation in its own direction", () => {
  const step = (s: string, t: string, rel: string) => ({ sourceCompanyId: s, sourceName: s.toUpperCase(), targetCompanyId: t, targetName: t.toUpperCase(), relationshipType: rel as never, edgeSource: "NEWS", strength: 1, evidence: [] });
  // anchor a; first edge b→a (b supplies a, so the arrow points back at a), then b competes with c (no direction)
  const node = { basis: "INFERRED" as const, companyId: "c", anchorCompanyId: "a", path: [step("b", "a", "SUPPLY"), step("b", "c", "COMPETE")] };
  assert.equal(pathText(node), "A ← 공급 ← B — 경쟁 — C");
  // a supplies b, b invests in d: arrows point outward
  const out = { basis: "INFERRED" as const, companyId: "d", anchorCompanyId: "a", path: [step("a", "b", "SUPPLY"), step("b", "d", "INVEST")] };
  assert.equal(pathText(out), "A → 공급 → B → 투자 → D");
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
  assert.equal(moveLine(move, true), "예상 변동폭: 이 종목은 뉴스가 있던 312거래일 동안 3거래일 기준 시장 대비 평균 ±6.2% 움직였습니다(오르든 내리든, 평소 변동폭에 가까움)");
  assert.match(moveLine(move, false) ?? "", /참고치로만/);
});

test("josa follows the last sound of the name", () => {
  assert.equal(josa("넥스틴", "은", "는"), "은");
  assert.equal(josa("SK하이닉스", "은", "는"), "는");
  assert.equal(josa("LG이노텍", "이", "가"), "이");
  assert.equal(josa("ISC", "과", "와"), "와");
  assert.equal(josa("KCC", "을", "를"), "를");
  assert.equal(josa("SKC", "은", "는"), "는");
});

test("why text explains how the news reaches the company", () => {
  const step = (s: string, t: string, rel: string) => ({ sourceCompanyId: s, sourceName: s, targetCompanyId: t, targetName: t, relationshipType: rel as never, edgeSource: "NEWS", strength: 1, evidence: [] });
  const industries: Record<string, string> = { 넥스틴: "반도체 장비", 한미반도체: "반도체 장비", SK하이닉스: "반도체 제조", 케이씨텍: "반도체 장비" };
  const industryOf = (name: string) => industries[name] ?? null;
  const node = (over: Record<string, unknown>) => ({ basis: "INFERRED", companyId: "x", anchorCompanyId: null, path: [], position: "SUPPLIER", name: "x", industry: null, ...over }) as never;

  assert.equal(
    whyText(node({ basis: "REPORTED", name: "한미반도체", position: "REPORTED" }), "EARNINGS_BEAT", industryOf),
    "한미반도체는 이 기사에 직접 나온 회사예요. 자기 회사의 실적 소식이라 주가에 가장 직접 반영돼요.",
  );
  assert.equal(
    whyText(node({ name: "넥스틴", companyId: "넥스틴", anchorCompanyId: "SK하이닉스", industry: "반도체 장비", path: [step("넥스틴", "SK하이닉스", "SUPPLY")] }), "TARIFF", industryOf),
    "넥스틴은 SK하이닉스에 장비를 공급하는 회사예요. SK하이닉스의 생산·수출에 차질이 생기면 넥스틴의 납품 일정도 함께 흔들릴 수 있어요.",
  );
  assert.equal(
    whyText(node({ name: "SK하이닉스", companyId: "SK하이닉스", anchorCompanyId: "한미반도체", position: "CUSTOMER", industry: "반도체 제조", path: [step("한미반도체", "SK하이닉스", "SUPPLY")] }), "EARNINGS_BEAT", industryOf),
    "SK하이닉스는 한미반도체의 장비를 사 가는 고객사예요. 한미반도체의 실적·수주가 좋다는 건 고객사인 SK하이닉스가 투자와 생산을 늘리고 있다는 신호로 읽혀요.",
  );
  assert.equal(
    whyText(node({ name: "넥스틴", companyId: "넥스틴", anchorCompanyId: "케이씨텍", industry: "반도체 장비", path: [step("케이씨텍", "SK하이닉스", "SUPPLY"), step("넥스틴", "SK하이닉스", "SUPPLY")] }), "SUPPLY_DEAL", industryOf),
    "넥스틴은 SK하이닉스에 장비를 공급하는 회사예요. SK하이닉스가 잘되거나 투자를 늘리면 넥스틴에 들어오는 주문도 늘 수 있어요. SK하이닉스는 이 기사의 케이씨텍과 공급 관계로 이어져 있어서, 그 소식이 SK하이닉스를 거쳐 넥스틴까지 닿아요.",
  );
  assert.match(
    whyText(node({ name: "DB하이텍", companyId: "DB하이텍", anchorCompanyId: "삼성전자", position: "COMPETITOR", path: [step("삼성전자", "DB하이텍", "COMPETE")] }), "OTHER", industryOf) ?? "",
    /^DB하이텍은 삼성전자와 같은 시장에서 경쟁하는 회사예요\./,
  );
});
