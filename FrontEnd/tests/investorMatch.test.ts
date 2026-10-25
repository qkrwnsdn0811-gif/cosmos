import { test } from "node:test";
import assert from "node:assert/strict";
import type { CompanyQuote } from "../src/api/types.ts";
import {
  HORIZON_QUESTION,
  SCORED_QUESTIONS,
  investorType,
  matchBasis,
  matchStocks,
} from "../src/features/match/investorMatch.ts";

const quote = (over: Partial<CompanyQuote>): CompanyQuote => ({
  companyId: "c", name: "기업", stockCode: "000000", market: "KOSPI", industry: "반도체 장비",
  close: 100, prevClose: 99, tradingDate: "2026-10-23", change1d: 0.01, change1m: 0.1, change3m: 0.2, spark: [90, 100],
  marketCap: 1e12, marketCapRank: 1, sizeClass: "LARGE", per: 10, pbr: 1, dividendYield: 0.01, dividendPerShare: 100,
  netIncomeTtm: 1e11, equity: 1e12, fiscalBasis: "기준", priceDate: "2026-10-23", relationCount: 1,
  news7d: 0, newsUsual7d: 0, high52w: 200, low52w: 50,
  expectedMoveShort: 0.05, moveGradeShort: "MID", expectedMoveLong: 0.12, moveGradeLong: "MID",
  ...over,
});
const names = (rows: { quote: CompanyQuote }[]) => rows.map((r) => r.quote.name);

test("four scored questions with five choices each, and a horizon question", () => {
  assert.equal(SCORED_QUESTIONS.length, 4);
  for (const q of SCORED_QUESTIONS) assert.deepEqual(q.options.map((o) => o.score), [1, 2, 3, 4, 5]);
  assert.deepEqual(HORIZON_QUESTION.options.map((o) => o.horizon), ["SHORT", "LONG"]);
});

test("score totals map to the five investor types at the agreed cut-offs", () => {
  assert.equal(investorType([1, 1, 1, 1]), "STABLE");
  assert.equal(investorType([2, 2, 2, 1]), "STABLE");
  assert.equal(investorType([2, 2, 2, 2]), "STABLE_PLUS");
  assert.equal(investorType([3, 3, 2, 2]), "STABLE_PLUS");
  assert.equal(investorType([3, 3, 3, 2]), "NEUTRAL");
  assert.equal(investorType([4, 3, 3, 3]), "NEUTRAL");
  assert.equal(investorType([4, 4, 3, 3]), "ACTIVE");
  assert.equal(investorType([4, 4, 4, 4]), "ACTIVE");
  assert.equal(investorType([5, 4, 4, 4]), "AGGRESSIVE");
  assert.equal(investorType([5, 5, 5, 5]), "AGGRESSIVE");
});

test("short horizon with validated grades picks by move grade", () => {
  const rows = [
    quote({ companyId: "a", name: "작음대형", moveGradeShort: "SMALL", expectedMoveShort: 0.04, sizeClass: "LARGE", marketCap: 5e12 }),
    quote({ companyId: "b", name: "작음소형", moveGradeShort: "SMALL", expectedMoveShort: 0.03, sizeClass: "SMALL", marketCap: 1e11 }),
    quote({ companyId: "c", name: "보통", moveGradeShort: "MID", expectedMoveShort: 0.05 }),
    quote({ companyId: "d", name: "큼", moveGradeShort: "LARGE", expectedMoveShort: 0.08, marketCap: 2e12 }),
    quote({ companyId: "e", name: "큼주목", moveGradeShort: "LARGE", expectedMoveShort: 0.07, marketCap: 1e11, news7d: 30, newsUsual7d: 3 }),
  ];
  // 안정형: 변동 작음 중 대형주 먼저
  assert.deepEqual(names(matchStocks(rows, "STABLE", "SHORT", true)), ["작음대형", "작음소형"]);
  // 안정추구형: 변동 작음, 변동폭 작은 순
  assert.deepEqual(names(matchStocks(rows, "STABLE_PLUS", "SHORT", true)), ["작음소형", "작음대형"]);
  assert.deepEqual(names(matchStocks(rows, "NEUTRAL", "SHORT", true)), ["보통"]);
  // 적극투자형: 변동 큼, 시가총액 큰 순 · 공격투자형: 변동 큼, 주목 종목 먼저
  assert.deepEqual(names(matchStocks(rows, "ACTIVE", "SHORT", true)), ["큼", "큼주목"]);
  assert.deepEqual(names(matchStocks(rows, "AGGRESSIVE", "SHORT", true)), ["큼주목", "큼"]);
});

test("long horizon, or short when grades did not hold, picks by indicators instead of grades", () => {
  const rows = [
    quote({ companyId: "a", name: "대형배당", sizeClass: "LARGE", dividendYield: 0.02, per: 12, marketCap: 9e12 }),
    quote({ companyId: "b", name: "대형무배당", sizeClass: "LARGE", dividendYield: 0, per: 8, marketCap: 8e12 }),
    quote({ companyId: "c", name: "중형고배당", sizeClass: "MID", dividendYield: 0.04, per: 20, marketCap: 1e12 }),
    quote({ companyId: "d", name: "소형적자", sizeClass: "SMALL", dividendYield: 0, per: null, netIncomeTtm: -1e9, marketCap: 1e11 }),
    quote({ companyId: "e", name: "소형주목", sizeClass: "SMALL", dividendYield: 0, per: 30, marketCap: 2e11, change1d: -0.07 }),
  ];
  // 안정형: 흑자 중 대형주 먼저, 배당 높은 순
  assert.deepEqual(names(matchStocks(rows, "STABLE", "LONG", false)), ["대형배당", "대형무배당", "중형고배당", "소형주목"]);
  // 안정추구형: 배당을 주는 흑자 기업, 배당 높은 순
  assert.deepEqual(names(matchStocks(rows, "STABLE_PLUS", "LONG", false)), ["중형고배당", "대형배당"]);
  // 위험중립형: 흑자, PER 낮은 순
  assert.deepEqual(names(matchStocks(rows, "NEUTRAL", "LONG", false)), ["대형무배당", "대형배당", "중형고배당", "소형주목"]);
  // 적극투자형: 흑자인 중소형주, 시가총액 큰 순
  assert.deepEqual(names(matchStocks(rows, "ACTIVE", "LONG", false)), ["중형고배당", "소형주목"]);
  // 공격투자형: 중소형주 중 주목 종목 먼저
  assert.deepEqual(names(matchStocks(rows, "AGGRESSIVE", "LONG", false)).slice(0, 1), ["소형주목"]);
  // 단기라도 등급이 검증되지 않았으면 같은 지표 규칙
  assert.deepEqual(names(matchStocks(rows, "NEUTRAL", "SHORT", false)), names(matchStocks(rows, "NEUTRAL", "LONG", false)));
});

test("stocks without a price are left out, and at most six are returned", () => {
  const rows = Array.from({ length: 9 }, (_, i) => quote({ companyId: `s${i}`, name: `종목${i}`, moveGradeShort: "SMALL", marketCap: (9 - i) * 1e11 }));
  rows.push(quote({ companyId: "x", name: "주가없음", moveGradeShort: "SMALL", close: null, marketCap: 1e14 }));
  const picked = matchStocks(rows, "STABLE_PLUS", "SHORT", true);
  assert.equal(picked.length, 6);
  assert.ok(!names(picked).includes("주가없음"));
});

test("reason chips state facts only: graded move, size, dividend, PER, notable", () => {
  const big = quote({ name: "대형", moveGradeShort: "SMALL", expectedMoveShort: 0.038, sizeClass: "LARGE", dividendYield: 0.002 });
  assert.deepEqual(matchStocks([big], "STABLE", "SHORT", true)[0].reasons, ["변동 작음 ±3.8%", "대형주", "배당 0.2%"]);
  const mid = quote({ name: "중형", per: 5.83, sizeClass: "MID" });
  assert.deepEqual(matchStocks([mid], "NEUTRAL", "LONG", false)[0].reasons, ["PER 5.8배", "중형주"]);
  const hot = quote({ name: "주목", moveGradeShort: "LARGE", expectedMoveShort: 0.069, sizeClass: "SMALL", news7d: 33, newsUsual7d: 3 });
  assert.deepEqual(matchStocks([hot], "AGGRESSIVE", "SHORT", true)[0].reasons, ["변동 큼 ±6.9%", "뉴스 급증 11배", "소형주"]);
  for (const r of matchStocks([big, mid, hot], "AGGRESSIVE", "SHORT", true).flatMap((p) => p.reasons)) {
    assert.doesNotMatch(r, /추천|매수|오를|상승 예상/);
  }
});

test("the basis line says how the list was picked and why long uses indicators", () => {
  assert.match(matchBasis("STABLE", "SHORT", true), /변동 작음/);
  assert.match(matchBasis("AGGRESSIVE", "SHORT", true), /주목 종목/);
  assert.match(matchBasis("STABLE_PLUS", "LONG", false), /배당/);
  assert.match(matchBasis("NEUTRAL", "LONG", false), /장기 변동폭 등급은 검증에서 순서가 맞지 않아/);
  assert.match(matchBasis("NEUTRAL", "SHORT", false), /단기 변동폭 등급이 검증에서 맞지 않아/);
});
