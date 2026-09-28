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

/**
 * 뉴스 요약(summary)이 크롤링 원문을 거의 그대로 담고 있어, 앞뒤에 기사 확인에 방해가 되는
 * 잡음이 붙어 오는 경우가 많다 — 표시 직전에만 걷어내고 원본 데이터(백엔드 summary)는 바꾸지 않는다.
 *  - 앞: "[포인트데일리 송가영 기자]", "(서울=뉴시스)", "【전남광주=뉴시스】" 같은 괄호 크레딧을 최대 3개까지 반복해서 뗀다
 *  - 뒤: "저작권자 ⓒ …", "무단전재", "재배포 금지" 같은 저작권 고지가 실제로 글 끝부분에 나오는 경우에만
 *    그 지점부터 통째로 자른다. 단순히 "첫 번째로 매칭되는 지점부터 끝까지"를 자르면, 기사 본문
 *    중간에 우연히 같은 문구(예: 저작권 정책을 다루는 기사에서 "무단 전재"라는 단어 자체가 본문
 *    내용인 경우)가 나올 때 본문이 심하게 잘려나가거나, 요약 전체가 저작권 고지만으로 이뤄진 경우
 *    빈 문자열로 사라져 버리는 문제가 있었다 — 매칭된 문구 뒤에 남은 글자 수가 짧을 때만(=진짜
 *    말미의 저작권 고지일 때만) 잘라낸다.
 *  - 뒤: 문장 끝에 그대로 붙어 온 언론사 URL 을 제거한다
 */
const LEADING_CREDIT = /^\s*[[［(（【][^\]）)】]{1,40}[\])）】]\s*/;
/**
 * 언론사마다 표기가 제각각이라(저작권자ⓒ/©/(c), 무단전재·배포·복제, 재배포·재판매 금지,
 * 영문 Copyright·All rights reserved, 최근엔 AI 학습 금지 고지까지) 문구를 하나씩 대응하다
 * 보면 끝이 없다 — 대신 아래 각 문구를 폭넓게 잡고, 실제로 잘라낼지는 stripTrailingMark 의
 * "문구 뒤에 남은 글자 수" 판정에 맡긴다. 바로 뒤 [ⓒ©] 단독 패턴도 포함하는데, 본문 중간에
 * 상표 등으로 등장해도 뒤에 남은 글자가 많으면 잘리지 않으므로 오탐 위험은 낮다.
 */
const TRAILING_MARK =
  /저작권자\s*(?:[ⓒ©]|\(c\))|[ⓒ©]|무단\s*(?:전재|배포|복제)|재배포\s*금지|재판매\s*금지|Copyright|All\s+rights\s+reserved|AI\s*(?:학습|데이터\s*활용).{0,15}금지/gi;
const TRAILING_URL = /\s*https?:\/\/\S+\s*$/gi;
/** 저작권 고지 문구 뒤에 이 글자 수보다 적게 남아 있어야 "말미의 고지"로 보고 잘라낸다 */
const TRAILING_MARK_TAIL_LIMIT = 40;

/**
 * 저작권/재배포 고지 문구 중 실제로 글 말미에 붙어 있는 것만 그 지점부터 잘라낸다.
 * 문구가 여러 번 나오면 앞에서부터 훑어, 그 뒤에 남는 글자 수가 짧은 첫 매칭 지점을 자르는
 * 시작점으로 삼는다 — 그래야 "저작권자 ⓒ 연합뉴스, 무단 전재 및 재배포 금지." 처럼 고지 문구가
 * 이어질 때 앞부분("저작권자 ⓒ …")부터 통째로 걷어낼 수 있다. 뒤에 남는 글자 수가 길면(=본문
 * 내용 중 우연히 등장한 단어로 판단) 잘라내지 않고 그대로 둔다.
 */
function stripTrailingMark(text: string): string {
  const matches = Array.from(text.matchAll(TRAILING_MARK));
  for (const m of matches) {
    if (m.index === undefined) continue;
    const end = m.index + m[0].length;
    const tail = text.slice(end).replace(TRAILING_URL, "").trim();
    if (tail.length <= TRAILING_MARK_TAIL_LIMIT) {
      return text.slice(0, m.index).trim();
    }
  }
  return text;
}

export function cleanNewsSummary(text: string | null | undefined): string {
  if (!text) return "";
  let out = text;
  for (let i = 0; i < 3 && LEADING_CREDIT.test(out); i += 1) {
    out = out.replace(LEADING_CREDIT, "");
  }
  const beforeTrailingCut = out;
  out = stripTrailingMark(out).replace(TRAILING_URL, "").trim();
  // 저작권 고지 판정이 너무 공격적으로 걸려 결과가 거의 남지 않으면(=요약 전체가 고지문 뿐이었던
  // 경우), 빈 화면보다는 고지 문구가 섞여 있더라도 원문 그대로 보여주는 쪽을 택한다.
  if (out.length < 5 && beforeTrailingCut.trim().length >= 5) {
    out = beforeTrailingCut.replace(TRAILING_URL, "").trim();
  }
  return out;
}

/** 뉴스 목록을 최신순(발행일 내림차순)으로 — publishedAt 이 없는 기사는 맨 뒤로 보낸다 */
export function sortNewsByLatest<T extends { publishedAt: string | null }>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    if (!a.publishedAt && !b.publishedAt) return 0;
    if (!a.publishedAt) return 1;
    if (!b.publishedAt) return -1;
    return new Date(b.publishedAt).getTime() - new Date(a.publishedAt).getTime();
  });
}

export function initials(name: string) {
  const latin = name.match(/[A-Za-z0-9]+/g);
  if (latin && latin.length > 1) return latin.slice(0, 2).map((w) => w[0]).join("").toUpperCase();
  if (latin?.[0] && latin[0].length === name.replace(/\s/g, "").length) return latin[0].slice(0, 2).toUpperCase();
  return name.replace(/\s+/g, "").slice(0, 2);
}
