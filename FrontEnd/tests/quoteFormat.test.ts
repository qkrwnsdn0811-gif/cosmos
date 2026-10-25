import { test } from "node:test";
import assert from "node:assert/strict";
import type { CompanyQuote } from "../src/api/types.ts";
import {
  alignByDate,
  applyPreset,
  fmtCap,
  fmtPct,
  fmtPrice,
  fmtRatio,
  fmtSigned,
  median,
  perShare,
  perText,
  rangePosition,
  relationLabel,
  sliceDays,
  sortQuotes,
  tone,
} from "../src/features/companies/quoteFormat.ts";

const quote = (over: Partial<CompanyQuote>): CompanyQuote => ({
  companyId: "c", name: "기업", stockCode: "000000", market: "KOSPI", industry: "반도체 장비",
  close: 100, prevClose: 99, tradingDate: "2026-10-23", change1d: 0.01, change1m: 0.1, change3m: 0.2, spark: [90, 100],
  marketCap: 1e12, marketCapRank: 1, sizeClass: "LARGE", per: 10, pbr: 1, dividendYield: 0.01, dividendPerShare: 100,
  netIncomeTtm: 1e11, equity: 1e12, fiscalBasis: "기준", priceDate: "2026-10-23", relationCount: 1,
  ...over,
});

test("prices, signed amounts and percents use the app's formats", () => {
  assert.equal(fmtPrice(1263000), "1,263,000원");
  assert.equal(fmtPrice(null), "-");
  assert.equal(fmtSigned(40000), "+40,000");
  assert.equal(fmtSigned(-1000), "−1,000");
  assert.equal(fmtSigned(0), "0");
  assert.equal(fmtPct(0.0327), "+3.27%");
  assert.equal(fmtPct(-0.012), "−1.20%");
  assert.equal(fmtPct(0), "0.00%");
  assert.equal(fmtPct(null), "-");
  assert.equal(fmtCap(922.61e12), "923조");
  assert.equal(fmtCap(42.7e12), "42.7조");
  assert.equal(fmtCap(932e8), "932억");
  assert.equal(tone(0.01), "rise");
  assert.equal(tone(-0.01), "fall");
  assert.equal(tone(0), "flat");
  assert.equal(tone(null), "flat");
});

test("perText and lowPer preset skip loss makers", () => {
  assert.equal(perText({ per: null, netIncomeTtm: -5 }), "적자");
  assert.equal(perText({ per: 5.61, netIncomeTtm: 1 }), "5.6배");
  assert.equal(perText({ per: null, netIncomeTtm: null }), "-");
  const rows = [
    quote({ companyId: "a", per: 30 }),
    quote({ companyId: "loss", per: null, netIncomeTtm: -1 }),
    quote({ companyId: "b", per: 8 }),
    quote({ companyId: "none", per: null, netIncomeTtm: null }),
  ];
  const { rows: picked, sort, dir } = applyPreset(rows, "lowPer");
  assert.deepEqual(picked.map((r) => r.companyId), ["b", "a"]);
  assert.equal(sort, "per");
  assert.equal(dir, "asc");
});

test("presets pick their sort and filter", () => {
  const rows = [
    quote({ companyId: "up", change1m: 0.3, dividendYield: 0 }),
    quote({ companyId: "down", change1m: -0.2, dividendYield: 0.03 }),
    quote({ companyId: "flat", change1m: null, dividendYield: null }),
  ];
  assert.deepEqual(applyPreset(rows, "up1m").rows.map((r) => r.companyId), ["up"]);
  assert.deepEqual(applyPreset(rows, "down1m").rows.map((r) => r.companyId), ["down"]);
  assert.deepEqual(applyPreset(rows, "dividend").rows.map((r) => r.companyId), ["down"]);
  assert.equal(applyPreset(rows, "all").rows.length, 3);
  assert.equal(applyPreset(rows, "related").sort, "relations");
});

test("median ignores nulls", () => {
  assert.equal(median([3, null, 1, 2]), 2);
  assert.equal(median([1, 2, 3, 4]), 2.5);
  assert.equal(median([null]), null);
});

test("per-share figures come from market cap and close", () => {
  assert.deepEqual(perShare({ marketCap: 1000, close: 10, netIncomeTtm: 50, equity: 200 }), { eps: 0.5, bps: 2, roe: 0.25 });
  assert.deepEqual(perShare({ marketCap: null, close: 10, netIncomeTtm: 50, equity: 200 }), { eps: null, bps: null, roe: 0.25 });
});

test("relation labels read from this company's side", () => {
  assert.equal(relationLabel("SUPPLY", "IN"), "공급사");
  assert.equal(relationLabel("SUPPLY", "OUT"), "고객사");
  assert.equal(relationLabel("INVEST", "IN"), "지분 보유사");
  assert.equal(relationLabel("INVEST", "OUT"), "지분 투자처");
  assert.equal(relationLabel("PARTNER", "NONE"), "협력사");
  assert.equal(relationLabel("COMPETE", "NONE"), "경쟁사");
  assert.equal(relationLabel("CO_MENTIONED", "NONE"), "함께 언급");
});

test("sortQuotes keeps nulls last in both directions", () => {
  const rows = [quote({ companyId: "n", change1d: null }), quote({ companyId: "a", change1d: 0.1 }), quote({ companyId: "b", change1d: -0.1 })];
  assert.deepEqual(sortQuotes(rows, "change1d", "desc").map((r) => r.companyId), ["a", "b", "n"]);
  assert.deepEqual(sortQuotes(rows, "change1d", "asc").map((r) => r.companyId), ["b", "a", "n"]);
  assert.deepEqual(sortQuotes([quote({ name: "나" }), quote({ name: "가" })], "name", "asc").map((r) => r.name), ["가", "나"]);
});

test("alignByDate keeps only common dates", () => {
  const stock = [{ date: "d1", close: 100 }, { date: "d2", close: 110 }, { date: "d3", close: 120 }];
  const index = [{ date: "d1", close: 50 }, { date: "d3", close: 55 }, { date: "d4", close: 60 }];
  const out = alignByDate(stock, index);
  assert.deepEqual(out.map((p) => p.date), ["d1", "d3"]);
  assert.equal(out[0].stockReturn, 0);
  assert.ok(Math.abs(out[1].stockReturn - 0.2) < 1e-12);
  assert.ok(Math.abs(out[1].indexReturn - 0.1) < 1e-12);
  assert.equal(out[1].indexClose, 55);
});

test("sliceDays and rangePosition", () => {
  const points = Array.from({ length: 80 }, (_, i) => i);
  assert.equal(sliceDays(points, "3M").length, 61);
  assert.equal(sliceDays(points, "3M")[60], 79);
  assert.equal(sliceDays(points.slice(0, 10), "1M").length, 10);
  assert.equal(rangePosition(10, 20, 15), 0.5);
  assert.equal(rangePosition(10, 10, 10), 0.5);
  assert.equal(rangePosition(10, 20, 25), 1);
});

test("fmtRatio shows unsigned ratios and multiples", () => {
  assert.equal(fmtRatio(0.00774), "0.77%");
  assert.equal(fmtRatio(0.25, 1), "25.0%");
  assert.equal(fmtRatio(null), "-");
});
