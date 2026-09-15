/**
 * 날짜·시각 표기. database-design.md 규칙대로 API 가 준 UTC 를 브라우저 시간대로 변환해 보여 준다.
 * 거래일(trading_at)처럼 '시각'이 아니라 '어느 날인가'가 본질인 값만 tz 를 고정해 읽는다(fmtTradingDate 참고).
 */
export function fmtDateTime(iso: string | null | undefined) {
  if (!iso) return "-";
  const d = new Date(iso);
  const p = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const g = (t: string) => p.find((x) => x.type === t)?.value ?? "";
  return `${g("year")}. ${g("month")}. ${g("day")}. ${g("hour")}:${g("minute")}`;
}

export function fmtDate(iso: string | null | undefined, tz?: string) {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("ko-KR", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
}

export function fmtShortDate(iso: string, tz?: string) {
  const d = new Date(iso);
  return new Intl.DateTimeFormat("ko-KR", { timeZone: tz, month: "numeric", day: "numeric" }).format(d);
}

export function fmtTime(iso: string) {
  return new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
}

/**
 * 거래일 표기. trading_at 은 '몇 시'가 아니라 '어느 거래일'이 본질이라 브라우저·시장 시간대로 변환하면
 * 하루가 밀린다(예: UTC 자정 값을 America/New_York 로 읽으면 전날 20시). 시장 현지 종가 시각(KRX 06:30Z,
 * NYSE 20:00Z 등)이든 날짜 전용 UTC 자정이든 UTC 로 읽으면 날짜 성분이 그대로 유지된다.
 */
export function fmtTradingDate(iso: string | null | undefined) {
  return fmtDate(iso, "UTC");
}

export function fmtRelative(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return "-";
  const diff = Math.max(0, now - new Date(iso).getTime());
  const m = Math.floor(diff / 60000);
  if (m < 1) return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}일 전`;
  return fmtDate(iso);
}

export function fmtNumber(n: number | null | undefined, digits = 0) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(n);
}

export function fmtCompact(n: number | null | undefined) {
  if (n === null || n === undefined) return "-";
  return new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

/** 시장별 거래 통화 (lib/meta.ts marketCurrency 가 시장 코드를 이 코드로 옮긴다) */
export type CurrencyCode = "KRW" | "USD" | "JPY" | "CNY";
/** 통화별 표기 규칙 — 원화·엔·위안은 조/억 단위가 통하는 ko-KR, 달러만 en-US(T/B/M) */
const CURRENCY_FORMAT: Record<CurrencyCode, { symbol: string; locale: string; digits: number; compactDigits: number }> = {
  KRW: { symbol: "₩", locale: "ko-KR", digits: 0, compactDigits: 1 },
  USD: { symbol: "$", locale: "en-US", digits: 2, compactDigits: 2 },
  JPY: { symbol: "¥", locale: "ko-KR", digits: 0, compactDigits: 1 },
  CNY: { symbol: "CN¥", locale: "ko-KR", digits: 2, compactDigits: 1 },
};

export function fmtPrice(n: number | null | undefined, currency: CurrencyCode = "KRW") {
  if (n === null || n === undefined) return "-";
  const f = CURRENCY_FORMAT[currency];
  return `${f.symbol}${new Intl.NumberFormat(f.locale, { maximumFractionDigits: f.digits, minimumFractionDigits: f.digits }).format(n)}`;
}

export function fmtPct(n: number | null | undefined, digits = 2, signed = true) {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  const s = n > 0 && signed ? "+" : "";
  return `${s}${n.toFixed(digits)}%`;
}

/** 0~1 비율을 백분율 정수로. 선택 필드가 null 이면 NaN% 대신 "-" 를 낸다. */
export function fmtPctFrom01(n: number | null | undefined, suffix = "%") {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return `${Math.round(n * 100)}${suffix}`;
}

/** 진행바 너비처럼 숫자가 반드시 필요한 자리 — null 은 0 으로 눕힌다. */
export function pct01(n: number | null | undefined) {
  if (n === null || n === undefined || Number.isNaN(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n * 100)));
}

/** 시가총액처럼 큰 금액 — 통화 기호 + 축약 표기 */
export function fmtCompactPrice(n: number | null | undefined, currency: CurrencyCode = "KRW") {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  const f = CURRENCY_FORMAT[currency];
  return `${f.symbol}${new Intl.NumberFormat(f.locale, { notation: "compact", maximumFractionDigits: f.compactDigits }).format(n)}`;
}

export function fmtScore(n: number | null | undefined) {
  if (n === null || n === undefined) return "-";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v));
}

/**
 * 동적으로 들어오는 이름 뒤에 붙는 조사(은/는, 이/가, 과/와 등)를 마지막 글자의 받침 유무로
 * 고른다. 한글 음절 범위가 아닌 마지막 글자(영문 약어 등)는 받침 없음으로 간주한다 — 완벽하진
 * 않지만 "TSMC는", "SK와"처럼 실제 발음과 맞아떨어지는 경우가 대부분이라 절충안으로 충분하다.
 */
export function hasBatchim(word: string): boolean {
  const ch = word.trim().slice(-1);
  const code = ch.charCodeAt(0);
  if (code < 0xac00 || code > 0xd7a3) return false;
  return (code - 0xac00) % 28 !== 0;
}
export function josa(word: string, withBatchim: string, withoutBatchim: string) {
  return hasBatchim(word) ? withBatchim : withoutBatchim;
}

export function initials(name: string) {
  const latin = name.match(/[A-Za-z0-9]+/g);
  if (latin && latin.length > 1) return latin.slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  if (latin?.[0] && latin[0].length === name.replace(/\s/g, "").length) return latin[0].slice(0, 2).toUpperCase();
  return name.replace(/\s+/g, "").slice(0, 2);
}
