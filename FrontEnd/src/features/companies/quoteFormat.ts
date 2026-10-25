/**
 * 기업 탭 시세판·기업 상세의 표시·계산 규칙. 화면 컴포넌트는 이 함수들만 부르고 숫자를 직접 다루지 않는다.
 * 숫자 표기는 이벤트 화면과 같다(fmtPct 의 '−', fmtWon 의 조·억). 오른다·내린다를 말하지 않고 관측값만 보여 준다.
 */
import type { CompanyQuote, IndexPoint, PricePeriod, RelationDirection, RelationshipType, StockPrices } from "@/api/types";
import { fmtPct as pct, fmtWon } from "../invest/investCopy.ts";

export const fmtCap = fmtWon;

/** 비율 → "+3.27%" · "−1.20%" · "0.00%" · "-" */
export function fmtPct(ratio: number | null | undefined, digits = 2): string {
  return pct(ratio, digits);
}

/** 부호 없는 비율 → "0.77%" (배당수익률·ROE) */
export function fmtRatio(ratio: number | null | undefined, digits = 2): string {
  return pct(ratio, digits, false);
}

export function fmtPrice(won: number | null | undefined): string {
  if (won == null || Number.isNaN(won)) return "-";
  if (won < 0) return `−${fmtPrice(-won)}`;
  return `${Math.round(won).toLocaleString("ko-KR")}원`;
}

/** 등락액 → "+40,000" · "−1,000" · "0" */
export function fmtSigned(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "-";
  const text = Math.round(Math.abs(v)).toLocaleString("ko-KR");
  if (text === "0") return "0";
  return `${v > 0 ? "+" : "−"}${text}`;
}

export type Tone = "rise" | "fall" | "flat";

export function tone(ratio: number | null | undefined): Tone {
  if (ratio == null || ratio === 0 || Number.isNaN(ratio)) return "flat";
  return ratio > 0 ? "rise" : "fall";
}

export const SIZE_LABEL: Record<string, string> = { LARGE: "대형", MID: "중형", SMALL: "소형" };

/** PER 칸: 순이익이 음수면 '적자' */
export function perText(q: Pick<CompanyQuote, "per" | "netIncomeTtm">): string {
  if (q.per != null && q.per > 0) return `${q.per.toFixed(1)}배`;
  if (q.netIncomeTtm != null && q.netIncomeTtm < 0) return "적자";
  return "-";
}

export function median(values: (number | null | undefined)[]): number | null {
  const xs = values.filter((v): v is number => v != null && !Number.isNaN(v)).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

/** 주당 수치 — 상장주식수 = 시가총액 ÷ 종가로 계산한 값이다(화면에 "계산값"이라고 적는다) */
export function perShare(q: Pick<CompanyQuote, "marketCap" | "close" | "netIncomeTtm" | "equity">): {
  eps: number | null;
  bps: number | null;
  roe: number | null;
} {
  const shares = q.marketCap != null && q.close != null && q.marketCap > 0 && q.close > 0 ? q.marketCap / q.close : null;
  return {
    eps: shares != null && q.netIncomeTtm != null ? q.netIncomeTtm / shares : null,
    bps: shares != null && q.equity != null ? q.equity / shares : null,
    roe: q.netIncomeTtm != null && q.equity != null && q.equity > 0 ? q.netIncomeTtm / q.equity : null,
  };
}

/** 관계 종류를 이 기업 쪽에서 읽은 말 (IN = 상대가 나에게 공급·투자) */
export function relationLabel(type: RelationshipType, direction: RelationDirection): string {
  if (type === "SUPPLY") return direction === "IN" ? "공급사" : direction === "OUT" ? "고객사" : "공급 관계";
  if (type === "INVEST") return direction === "IN" ? "지분 보유사" : direction === "OUT" ? "지분 투자처" : "지분 관계";
  if (type === "PARTNER") return "협력사";
  if (type === "COMPETE") return "경쟁사";
  if (type === "CO_MENTIONED") return "함께 언급";
  return "관계";
}

export type SortKey = "cap" | "change1d" | "change1m" | "per" | "pbr" | "dividend" | "relations" | "name";
export type SortDir = "asc" | "desc";

const SORT_VALUE: Record<Exclude<SortKey, "name">, (q: CompanyQuote) => number | null> = {
  cap: (q) => q.marketCap,
  change1d: (q) => q.change1d,
  change1m: (q) => q.change1m,
  per: (q) => (q.per != null && q.per > 0 ? q.per : null),
  pbr: (q) => q.pbr,
  dividend: (q) => q.dividendYield,
  relations: (q) => q.relationCount,
};

/** 값이 없는 행은 방향과 상관없이 맨 뒤 */
export function sortQuotes(rows: CompanyQuote[], key: SortKey, dir: SortDir): CompanyQuote[] {
  const sign = dir === "asc" ? 1 : -1;
  if (key === "name") return [...rows].sort((a, b) => sign * a.name.localeCompare(b.name, "ko"));
  const value = SORT_VALUE[key];
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null && vb == null) return a.name.localeCompare(b.name, "ko");
    if (va == null) return 1;
    if (vb == null) return -1;
    return sign * (va - vb) || a.name.localeCompare(b.name, "ko");
  });
}

export type PresetKey = "all" | "cap" | "up1m" | "down1m" | "lowPer" | "dividend" | "related";

/** 골라보기 칩 (토스증권 '주식 골라보기'의 묶음을 칩 한 줄로) */
export const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "cap", label: "시가총액 상위" },
  { key: "up1m", label: "1개월 상승" },
  { key: "down1m", label: "1개월 하락" },
  { key: "lowPer", label: "낮은 PER" },
  { key: "dividend", label: "배당주" },
  { key: "related", label: "관계 많은 기업" },
];

const PRESET_RULE: Record<PresetKey, { keep: (q: CompanyQuote) => boolean; sort: SortKey; dir: SortDir }> = {
  all: { keep: () => true, sort: "cap", dir: "desc" },
  cap: { keep: (q) => q.marketCap != null, sort: "cap", dir: "desc" },
  up1m: { keep: (q) => q.change1m != null && q.change1m > 0, sort: "change1m", dir: "desc" },
  down1m: { keep: (q) => q.change1m != null && q.change1m < 0, sort: "change1m", dir: "asc" },
  lowPer: { keep: (q) => q.per != null && q.per > 0 && !(q.netIncomeTtm != null && q.netIncomeTtm < 0), sort: "per", dir: "asc" },
  dividend: { keep: (q) => q.dividendYield != null && q.dividendYield > 0, sort: "dividend", dir: "desc" },
  related: { keep: (q) => q.relationCount > 0, sort: "relations", dir: "desc" },
};

/** 칩 하나 = 거르기 + 정렬. 돌려준 rows 는 이미 그 정렬로 되어 있다 */
export function applyPreset(rows: CompanyQuote[], preset: PresetKey): { rows: CompanyQuote[]; sort: SortKey; dir: SortDir } {
  const rule = PRESET_RULE[preset];
  return { rows: sortQuotes(rows.filter(rule.keep), rule.sort, rule.dir), sort: rule.sort, dir: rule.dir };
}

const PERIOD_DAYS: Record<PricePeriod, number> = { "1M": 20, "3M": 60, "6M": 120, "1Y": 250 };

/** 기간의 마지막 N+1개 (기준점 하나 포함) */
export function sliceDays<T>(points: T[], period: PricePeriod): T[] {
  return points.slice(-(PERIOD_DAYS[period] + 1));
}

export interface AlignedPoint {
  date: string;
  close: number;
  indexClose: number;
  stockReturn: number;
  indexReturn: number;
}

/** 주가와 지수가 모두 있는 날만, 첫 공통일 기준 누적 수익률 */
export function alignByDate(stock: { date: string; close: number }[], index: { date: string; close: number }[]): AlignedPoint[] {
  const byDate = new Map(index.map((p) => [p.date, p.close]));
  const common = stock.filter((p) => byDate.has(p.date));
  if (!common.length) return [];
  const base = common[0].close;
  const indexBase = byDate.get(common[0].date)!;
  return common.map((p) => {
    const indexClose = byDate.get(p.date)!;
    return { date: p.date, close: p.close, indexClose, stockReturn: p.close / base - 1, indexReturn: indexClose / indexBase - 1 };
  });
}

export interface Daily {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * 일봉을 날짜순으로. 다른 기업의 응답(종목을 옮길 때 react-query 가 잠깐 보여 주는 이전 데이터)이면 빈 목록 —
 * 새 종목 이름 아래 이전 종목 숫자가 보이지 않게 한다
 */
export function dailyCandles(prices: StockPrices | undefined, companyId: string): Daily[] {
  if (!prices || prices.companyId !== companyId) return [];
  return prices.items
    .map((c) => ({ date: c.tradingAt.slice(0, 10), open: c.openPrice, high: c.highPrice, low: c.lowPrice, close: c.closePrice, volume: c.tradingVolume }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

/**
 * 상세 차트에 그릴 점. 지수와 비교하려는데 지수가 없으면(불러오기 실패·겹치는 날 없음) 주가 모드로 그리고 indexMissing 을 켠다
 */
export function chartSeries(
  daily: { date: string; close: number }[],
  index: IndexPoint[],
  period: PricePeriod,
  compare: boolean,
): { points: AlignedPoint[]; mode: "compare" | "price"; indexMissing: boolean } {
  const stock = sliceDays(daily, period).map((d) => ({ date: d.date, close: d.close }));
  if (compare) {
    const aligned = alignByDate(stock, index);
    if (aligned.length >= 2) return { points: aligned, mode: "compare", indexMissing: false };
  }
  const base = stock[0]?.close ?? 1;
  const points = stock.map((p) => ({ ...p, indexClose: 0, stockReturn: p.close / base - 1, indexReturn: 0 }));
  return { points, mode: "price", indexMissing: compare };
}

/** 퍼센트 눈금 간격(비율)에 맞는 소수 자리 — 0.5% 간격을 "1%"로 반올림하지 않게 */
export function pctTickDigits(step: number): number {
  const pct = step * 100;
  return pct >= 1 ? 0 : Math.ceil(-Math.log10(pct) - 1e-9);
}

/** 저가—고가 막대 위 위치 0~1 (범위가 0이면 가운데) */
export function rangePosition(low: number, high: number, v: number): number {
  if (high <= low) return 0.5;
  return Math.min(1, Math.max(0, (v - low) / (high - low)));
}
