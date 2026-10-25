/**
 * 투자 성향 간이 진단 → 성향·기간에 맞는 반도체 종목 고르기. 화면(MatchDialog)은 이 함수들만 부른다.
 * 추천·매수 권유가 아니라 조건 맞춤이다: 단기는 검증을 통과한 예상 변동폭 등급으로, 장기(등급 순서가 맞지 않음)나
 * 단기 등급이 검증되지 않은 때는 규모·배당·PER·흑자·주목 같은 현재 지표로 고른다. 이유 칩에는 관측값만 쓴다.
 */
import type { CompanyQuote, Horizon, MoveGrade } from "@/api/types";
import { SIZE_LABEL, fmtRatio, watchReasons, watchScore } from "../companies/quoteFormat.ts";
import { MOVE_GRADE_LABEL } from "../invest/investCopy.ts";

export type InvestorType = "STABLE" | "STABLE_PLUS" | "NEUTRAL" | "ACTIVE" | "AGGRESSIVE";

export const TYPE_INFO: Record<InvestorType, { label: string; desc: string }> = {
  STABLE: { label: "안정형", desc: "원금을 지키는 것이 가장 중요해요. 크게 흔들리지 않는 종목부터 봅니다." },
  STABLE_PLUS: { label: "안정추구형", desc: "손실은 작게, 수익은 예금보다 조금 더. 변동이 작은 종목을 봅니다." },
  NEUTRAL: { label: "위험중립형", desc: "어느 정도 오르내림은 감수해요. 변동이 중간인 종목을 봅니다." },
  ACTIVE: { label: "적극투자형", desc: "손실 위험이 있어도 더 큰 수익을 노려요. 크게 움직이는 종목을 봅니다." },
  AGGRESSIVE: { label: "공격투자형", desc: "큰 변동도 괜찮아요. 크게 움직이고 지금 눈여겨볼 사실이 있는 종목을 봅니다." },
};

export interface MatchQuestion {
  id: string;
  text: string;
  options: { label: string; score: number }[];
}

/** 점수 문항 — 선택지 점수 1~5, 합계로 유형을 정한다 */
export const SCORED_QUESTIONS: MatchQuestion[] = [
  {
    id: "experience",
    text: "주식 투자 경험은 어느 정도인가요?",
    options: [
      { label: "없어요", score: 1 },
      { label: "예금·적금·펀드 정도", score: 2 },
      { label: "주식 1년 미만", score: 3 },
      { label: "주식 1~3년", score: 4 },
      { label: "주식 3년 이상", score: 5 },
    ],
  },
  {
    id: "drawdown",
    text: "산 주식이 한 달 새 10% 떨어지면 어떻게 하시겠어요?",
    options: [
      { label: "바로 판다", score: 1 },
      { label: "일부를 판다", score: 2 },
      { label: "조금 더 기다린다", score: 3 },
      { label: "그대로 둔다", score: 4 },
      { label: "더 산다", score: 5 },
    ],
  },
  {
    id: "loss",
    text: "견딜 수 있는 손실은 어느 정도인가요?",
    options: [
      { label: "원금 손실은 싫다", score: 1 },
      { label: "5% 이내", score: 2 },
      { label: "10% 이내", score: 3 },
      { label: "20% 이내", score: 4 },
      { label: "그 이상도 괜찮다", score: 5 },
    ],
  },
  {
    id: "goal",
    text: "투자 목표는 무엇에 가까운가요?",
    options: [
      { label: "원금 지키기", score: 1 },
      { label: "예금보다 조금 더", score: 2 },
      { label: "시장 평균만큼", score: 3 },
      { label: "시장보다 더", score: 4 },
      { label: "위험이 커도 큰 수익", score: 5 },
    ],
  },
];

/** 기간 문항 — 점수 없이 화면의 단기(3거래일)·장기(20거래일)를 고른다 */
export const HORIZON_QUESTION: { text: string; options: { label: string; horizon: Horizon }[] } = {
  text: "얼마나 들고 갈 생각인가요?",
  options: [
    { label: "단기 — 며칠", horizon: "SHORT" },
    { label: "장기 — 한 달 이상", horizon: "LONG" },
  ],
};

/** 점수 합계(4~20) → 유형: 7 이하 · 8~10 · 11~13 · 14~16 · 17 이상 */
export function investorType(scores: number[]): InvestorType {
  const total = scores.reduce((a, b) => a + b, 0);
  if (total <= 7) return "STABLE";
  if (total <= 10) return "STABLE_PLUS";
  if (total <= 13) return "NEUTRAL";
  if (total <= 16) return "ACTIVE";
  return "AGGRESSIVE";
}

export interface MatchPick {
  quote: CompanyQuote;
  reasons: string[];
}

export const MATCH_LIMIT = 6;

const profitable = (q: CompanyQuote) => q.netIncomeTtm != null && q.netIncomeTtm > 0;
const smallOrMid = (q: CompanyQuote) => q.sizeClass === "MID" || q.sizeClass === "SMALL";
const large = (q: CompanyQuote) => (q.sizeClass === "LARGE" ? 0 : 1);
const cap = (q: CompanyQuote) => q.marketCap ?? -1;
const grade = (q: CompanyQuote, horizon: Horizon): MoveGrade | null => (horizon === "SHORT" ? q.moveGradeShort : q.moveGradeLong);
const move = (q: CompanyQuote, horizon: Horizon): number | null => (horizon === "SHORT" ? q.expectedMoveShort : q.expectedMoveLong);
/** 주목 정도 큰 순, 주목이 아니면 뒤로 */
const notable = (q: CompanyQuote) => watchScore(q) ?? -1;

type Rule = { keep: (q: CompanyQuote) => boolean; order: (a: CompanyQuote, b: CompanyQuote) => number };

function gradedRule(type: InvestorType, horizon: Horizon): Rule {
  const is = (g: MoveGrade) => (q: CompanyQuote) => grade(q, horizon) === g;
  const byMove = (a: CompanyQuote, b: CompanyQuote) => (move(a, horizon) ?? Infinity) - (move(b, horizon) ?? Infinity);
  switch (type) {
    case "STABLE":
      return { keep: is("SMALL"), order: (a, b) => large(a) - large(b) || byMove(a, b) || cap(b) - cap(a) };
    case "STABLE_PLUS":
      return { keep: is("SMALL"), order: (a, b) => byMove(a, b) || cap(b) - cap(a) };
    case "NEUTRAL":
      return { keep: is("MID"), order: (a, b) => cap(b) - cap(a) };
    case "ACTIVE":
      return { keep: is("LARGE"), order: (a, b) => cap(b) - cap(a) };
    case "AGGRESSIVE":
      return { keep: is("LARGE"), order: (a, b) => notable(b) - notable(a) || -byMove(a, b) || cap(b) - cap(a) };
  }
}

function indicatorRule(type: InvestorType): Rule {
  const dividend = (q: CompanyQuote) => q.dividendYield ?? 0;
  switch (type) {
    case "STABLE":
      return { keep: profitable, order: (a, b) => large(a) - large(b) || dividend(b) - dividend(a) || cap(b) - cap(a) };
    case "STABLE_PLUS":
      return { keep: (q) => profitable(q) && dividend(q) > 0, order: (a, b) => dividend(b) - dividend(a) || cap(b) - cap(a) };
    case "NEUTRAL":
      return { keep: (q) => profitable(q) && q.per != null && q.per > 0, order: (a, b) => a.per! - b.per! || cap(b) - cap(a) };
    case "ACTIVE":
      return { keep: (q) => profitable(q) && smallOrMid(q), order: (a, b) => cap(b) - cap(a) };
    case "AGGRESSIVE":
      return { keep: smallOrMid, order: (a, b) => notable(b) - notable(a) || cap(b) - cap(a) };
  }
}

function reasonsFor(q: CompanyQuote, type: InvestorType, horizon: Horizon, graded: boolean): string[] {
  const g = grade(q, horizon);
  const m = move(q, horizon);
  const moveChip = graded && g && m != null ? `${MOVE_GRADE_LABEL[g]} ±${fmtRatio(m, 1)}` : null;
  const sizeChip = q.sizeClass ? `${SIZE_LABEL[q.sizeClass]}주` : null;
  const dividendChip = q.dividendYield != null && q.dividendYield > 0 ? `배당 ${fmtRatio(q.dividendYield, 1)}` : null;
  const perChip = q.per != null && q.per > 0 ? `PER ${q.per.toFixed(1)}배` : null;
  const watch = watchReasons(q).map((r) => r.label);
  const chips: Record<InvestorType, (string | null)[]> = {
    STABLE: [moveChip, sizeChip, dividendChip],
    STABLE_PLUS: [moveChip, dividendChip, sizeChip],
    NEUTRAL: [moveChip, perChip, sizeChip],
    ACTIVE: [moveChip, sizeChip, watch[0] ?? null],
    AGGRESSIVE: [moveChip, ...watch, sizeChip],
  };
  return chips[type].filter((c): c is string => c != null).slice(0, 3);
}

/**
 * 성향·기간에 맞는 종목(주가 있는 것만, 최대 MATCH_LIMIT). graded = 이 기간의 변동폭 등급이 검증을 통과했는지
 * (investCopy.gradesHold) — 통과했으면 등급으로, 아니면 지표로 고른다.
 */
export function matchStocks(quotes: CompanyQuote[], type: InvestorType, horizon: Horizon, graded: boolean): MatchPick[] {
  const rule = graded ? gradedRule(type, horizon) : indicatorRule(type);
  return quotes
    .filter((q) => q.close != null && rule.keep(q))
    .sort(rule.order)
    .slice(0, MATCH_LIMIT)
    .map((q) => ({ quote: q, reasons: reasonsFor(q, type, horizon, graded) }));
}

const GRADED_BASIS: Record<InvestorType, string> = {
  STABLE: "단기 예상 변동폭이 작은 '변동 작음' 종목 중 대형주 먼저",
  STABLE_PLUS: "단기 예상 변동폭이 작은 '변동 작음' 종목, 변동폭 작은 순",
  NEUTRAL: "단기 예상 변동폭이 중간인 '변동 보통' 종목, 시가총액 큰 순",
  ACTIVE: "단기 예상 변동폭이 큰 '변동 큼' 종목, 시가총액 큰 순",
  AGGRESSIVE: "단기 예상 변동폭이 큰 '변동 큼' 종목 중 주목 종목 먼저",
};
const INDICATOR_BASIS: Record<InvestorType, string> = {
  STABLE: "흑자 기업 중 대형주 먼저, 배당 높은 순",
  STABLE_PLUS: "배당을 주는 흑자 기업, 배당 높은 순",
  NEUTRAL: "흑자 기업, PER 낮은 순",
  ACTIVE: "흑자인 중소형주, 시가총액 큰 순",
  AGGRESSIVE: "중소형주 중 주목 종목 먼저",
};

/** 결과 화면의 '이렇게 골랐어요' 한 줄 */
export function matchBasis(type: InvestorType, horizon: Horizon, graded: boolean): string {
  if (graded) return GRADED_BASIS[type];
  const why =
    horizon === "LONG"
      ? "장기 변동폭 등급은 검증에서 순서가 맞지 않아 지표로 골랐어요."
      : "단기 변동폭 등급이 검증에서 맞지 않아 지표로 골랐어요.";
  return `${INDICATOR_BASIS[type]} · ${why}`;
}

/* ------------------------------ 저장 ------------------------------ */
export interface SavedMatch {
  type: InvestorType;
  horizon: Horizon;
}
const STORE_KEY = "nexus.investor-match";

/** 지난 진단 결과(브라우저 저장). 없거나 읽을 수 없으면 null */
export function loadMatch(): SavedMatch | null {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<SavedMatch>;
    if (!v.type || !(v.type in TYPE_INFO) || (v.horizon !== "SHORT" && v.horizon !== "LONG")) return null;
    return { type: v.type, horizon: v.horizon };
  } catch {
    return null;
  }
}

export function saveMatch(match: SavedMatch | null): void {
  try {
    if (match) localStorage.setItem(STORE_KEY, JSON.stringify(match));
    else localStorage.removeItem(STORE_KEY);
  } catch {
    /* 저장 불가 환경 — 이번 화면에서만 쓴다 */
  }
}
