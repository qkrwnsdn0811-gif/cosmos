/**
 * 투자자 화면 문구와 숫자 표기 — 딱지·과거 사례·예상 변동폭·지표를 어떤 말로 보여 줄지 한곳에서 정한다.
 * 딱지는 과거 사례 통계라서 언제나 "과거 …"로 쓰고, 예측처럼 읽히는 말("오른다", "추천")은 쓰지 않는다.
 * import 가 타입뿐인 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/investCopy.test.ts).
 */
import type {
  EventNode,
  EventSignal,
  ExpectedMove,
  Fundamental,
  Horizon,
  MoveBacktestRow,
  MoveGrade,
  NewsTypeCode,
  SignalCount,
  SignalLabel,
} from "@/api/types";

export const DISCLAIMER = "과거 사례 통계이며 투자 권유가 아닙니다. 초과수익률은 이미 관측된 주가 변화이고 미래 주가 예측이 아닙니다.";

export const HORIZONS: Record<Horizon, { label: string; days: number; hint: string }> = {
  SHORT: { label: "단기", days: 3, hint: "뉴스 뒤 3거래일 반응" },
  LONG: { label: "장기", days: 20, hint: "뉴스 뒤 20거래일 반응" },
};

/** 뉴스 유형 라벨 — nexus/nexus/cases/newstype.py 의 TYPES 순서·문구와 같다 (필터 목록에 쓴다) */
export const NEWS_TYPES: { code: NewsTypeCode; label: string }[] = [
  { code: "EARNINGS_BEAT", label: "실적 호조" },
  { code: "EARNINGS_MISS", label: "실적 부진" },
  { code: "SUPPLY_DEAL", label: "수주·공급 계약" },
  { code: "EXPORT_CONTROL", label: "수출 규제·제재" },
  { code: "TARIFF", label: "관세·통상" },
  { code: "ACCIDENT", label: "사고·공급 차질" },
  { code: "PRICE_UP", label: "제품 가격 상승" },
  { code: "PRICE_DOWN", label: "제품 가격 하락" },
  { code: "PRODUCTION_CUT", label: "감산·재고 조정" },
  { code: "CAPEX", label: "증설·투자" },
  { code: "SHAREHOLDER", label: "주주환원" },
  { code: "AI_DEMAND", label: "AI·HBM 수요" },
  { code: "POLICY", label: "정부 정책·지원" },
  { code: "ANALYST", label: "증권가 전망·목표가" },
  { code: "MARKET_WRAP", label: "증시 시황" },
  { code: "OTHER", label: "기타" },
];

export type Tone = "up" | "down" | "hold";

export function toneOf(label: SignalLabel | null | undefined): Tone {
  return label === "UP" ? "up" : label === "DOWN" ? "down" : "hold";
}

/** 비율(0.0123) → "+1.2%" 처럼. sign=false 면 부호 없이 */
export function fmtPct(ratio: number | null | undefined, digits = 1, sign = true): string {
  if (ratio == null || Number.isNaN(ratio)) return "-";
  const v = ratio * 100;
  const text = Math.abs(v).toFixed(digits);
  if (!sign) return `${text}%`;
  if (Number(text) === 0) return `${(0).toFixed(digits)}%`;
  return `${v > 0 ? "+" : "−"}${text}%`;
}

/** 원 단위 시가총액 → "1,260조" · "8.4조" · "6,138억" */
export function fmtWon(won: number | null | undefined): string {
  if (won == null || Number.isNaN(won)) return "-";
  const jo = won / 1e12;
  if (jo >= 100) return `${Math.round(jo).toLocaleString("ko-KR")}조`;
  if (jo >= 1) return `${jo.toFixed(1)}조`;
  return `${Math.round(won / 1e8).toLocaleString("ko-KR")}억`;
}

/** 딱지 글자 — 과거 사례를 말한다는 것을 글자로도 드러낸다 */
export function signalText(signal: Pick<EventSignal, "label" | "holdReason"> | null | undefined): string {
  if (!signal) return "판단 보류";
  if (signal.label === "UP") return "과거 상승 우세";
  if (signal.label === "DOWN") return "과거 하락 우세";
  return signal.holdReason ? `판단 보류 · ${signal.holdReason}` : "판단 보류";
}

export function signalFor(node: Pick<EventNode, "signals">, horizon: Horizon): EventSignal | null {
  return node.signals.find((s) => s.horizon === horizon) ?? null;
}

/**
 * 과거 사례 한 줄 — "과거 '실적 부진' 뉴스 뒤 공급사 120건: 78건(65%) 하락, 3거래일 평균 초과수익률 −2.1%".
 * 사례가 없으면 null (판단 보류 사유가 이미 설명한다)
 */
export function pastLine(signal: EventSignal | null, typeLabel: string | null, positionLabel: string | null): string | null {
  if (!signal || signal.caseCount <= 0 || signal.downShare == null) return null;
  const down = Math.round(signal.downShare * signal.caseCount);
  const who = signal.caseScope === "COMPANY" ? "이 종목" : positionLabel ?? "같은 위치 기업";
  const type = typeLabel ? `'${typeLabel}' ` : "";
  return `과거 ${type}뉴스 뒤 ${who} ${signal.caseCount.toLocaleString("ko-KR")}건: ${down.toLocaleString("ko-KR")}건(${fmtPct(signal.downShare, 0, false)}) 하락, ${signal.windowDays}거래일 평균 초과수익률 ${fmtPct(signal.meanAr)}`;
}

/** 피드 요약 — "하락 우세 2 · 상승 우세 1" (0 인 항목은 뺀다). 판단 보류 수는 피드에 쓰지 않는다 */
export function countsText(counts: SignalCount | null | undefined): string {
  if (!counts) return "";
  const parts = [counts.down ? `하락 우세 ${counts.down}` : null, counts.up ? `상승 우세 ${counts.up}` : null].filter(Boolean);
  return parts.join(" · ");
}

export const SIZE_LABEL: Record<NonNullable<Fundamental["sizeClass"]>, string> = { LARGE: "대형", MID: "중형", SMALL: "소형" };

/**
 * 지표 한 줄 — "대형 · 시총 1,260조 · PER 12.3 · PBR 1.4 · 배당 1.2%". 장기 투자에서는 PER·PBR·배당을 앞에 둔다.
 * 재무 값이 없으면 그 항목만 뺀다 (순이익이 있는데 PER 이 없으면 적자)
 */
export function metricsText(f: Fundamental | null | undefined, horizon: Horizon): string {
  if (!f) return "";
  const size = f.sizeClass ? SIZE_LABEL[f.sizeClass] : null;
  const cap = f.marketCap != null ? `시총 ${fmtWon(f.marketCap)}` : null;
  const per = f.per != null ? `PER ${f.per.toFixed(1)}` : f.netIncomeTtm != null && f.netIncomeTtm <= 0 ? "적자" : null;
  const pbr = f.pbr != null ? `PBR ${f.pbr.toFixed(2)}` : null;
  const div = f.dividendYield != null ? `배당 ${fmtPct(f.dividendYield, 1, false)}` : null;
  const valuation = [per, pbr, div];
  const order = horizon === "LONG" ? [...valuation, size, cap] : [size, cap, ...valuation];
  return order.filter(Boolean).join(" · ");
}

/** 종목 카드의 DOM id — 관계 지도에서 행성을 고르면 이 카드로 스크롤한다 */
export const stockCardId = (companyId: string) => `stock-${companyId}`;

export const MOVE_GRADE_LABEL: Record<MoveGrade, string> = { SMALL: "변동 작음", MID: "변동 보통", LARGE: "변동 큼" };

export function moveFor(node: Pick<EventNode, "expectedMoves">, horizon: Horizon): ExpectedMove | null {
  // 예상 변동폭 이전 API(필드 없음)에 붙어도 카드 전체가 깨지지 않게 한다
  return node.expectedMoves?.find((m) => m.horizon === horizon) ?? null;
}

/**
 * 등급이 시험 기간에 맞았는가 — 실제 변동폭이 작음 < 보통 < 큼 순으로 커졌을 때만 화면에 등급을 붙인다.
 * 파이프라인 보고서의 판정(nexus/nexus/cases/report.py grades_hold)과 같은 규칙이다
 */
export function gradesHold(rows: MoveBacktestRow[], horizon: Horizon): boolean {
  const realized = (["SMALL", "MID", "LARGE"] as const).map((g) => rows.find((r) => r.horizon === horizon && r.grade === g)?.realizedMove);
  return realized.every((v): v is number => v != null) && realized[0] < realized[1] && realized[1] < realized[2];
}

/** 카드 머리의 변동폭 배지 — "변동 큼 ±6.2%", 등급이 검증되지 않은 기간은 "변동폭 ±18.7%" */
export function moveBadge(move: ExpectedMove | null, graded: boolean): string | null {
  if (!move) return null;
  const size = `±${fmtPct(move.expectedMove, 1, false)}`;
  return graded ? `${MOVE_GRADE_LABEL[move.grade]} ${size}` : `변동폭 ${size}`;
}

/**
 * 카드 본문 한 줄 — 방향과 무관한 크기라는 것, 며칠로 잰 것인지, 검증되지 않았으면 참고치라는 것.
 * 반도체 종목은 거의 매 거래일 관련 뉴스가 있어 이 값은 사실상 종목의 평소 변동폭이다 — 뉴스 고유의 효과처럼 말하지 않는다
 */
export function moveLine(move: ExpectedMove | null, graded: boolean): string | null {
  if (!move) return null;
  const base = `예상 변동폭: 이 종목은 뉴스가 있던 ${move.caseCount.toLocaleString("ko-KR")}거래일 동안 ${move.windowDays}거래일 기준 시장 대비 평균 ±${fmtPct(move.expectedMove, 1, false)} 움직였습니다(오르든 내리든, 평소 변동폭에 가까움)`;
  return graded ? base : `${base}. 이 기간 등급은 검증에서 맞지 않아 참고치로만 보여 줍니다`;
}

/** 예상 변동폭 검증 띠의 한 줄. 검증 행이 없으면 null */
export function moveCheckText(rows: MoveBacktestRow[], horizon: Horizon): { line: string; holds: boolean } | null {
  const small = rows.find((r) => r.horizon === horizon && r.grade === "SMALL");
  const large = rows.find((r) => r.horizon === horizon && r.grade === "LARGE");
  if (!small || !large || small.realizedMove == null || large.realizedMove == null) return null;
  const holds = gradesHold(rows, horizon);
  const line = `'변동 큼' 종목 실제 ±${fmtPct(large.realizedMove, 1, false)} · '변동 작음' 종목 실제 ±${fmtPct(small.realizedMove, 1, false)}`;
  return { line, holds };
}

/** 받침 있는 소리로 끝나는 영문자·숫자 (엘·엠·엔·알, 영·일·삼·육·칠·팔) */
const BATCHIM_LATIN = /[LMNR013678]$/i;

/** 받침 유무로 조사를 고른다 — 한글은 마지막 글자의 받침, 영문·숫자는 읽는 소리로 */
export function josa(word: string, withBatchim: string, without: string): string {
  const last = word.trim().slice(-1);
  const code = last.charCodeAt(0) - 0xac00;
  const has = code >= 0 && code <= 11171 ? code % 28 !== 0 : BATCHIM_LATIN.test(last);
  return has ? withBatchim : without;
}

/** 업종 → 공급하는 것 ("반도체 장비" → "장비") */
const SUPPLIED: Record<string, string> = { 장비: "장비", 소재: "소재", 부품: "부품", 설계: "칩 설계", 후공정: "패키징·테스트", 제조: "반도체" };

function supplied(industry: string | null): string {
  const kind = industry?.split(" ").pop() ?? "";
  return SUPPLIED[kind] ?? "제품";
}

const DEMAND_UP = new Set(["EARNINGS_BEAT", "SUPPLY_DEAL", "AI_DEMAND", "CAPEX", "PRICE_UP"]);
const DEMAND_DOWN = new Set(["EARNINGS_MISS", "PRODUCTION_CUT", "PRICE_DOWN"]);
const SUPPLY_SHOCK = new Set(["ACCIDENT", "EXPORT_CONTROL", "TARIFF"]);

/** 기사에 직접 나온 회사에게 이 유형의 뉴스가 왜 중요한가 */
const REPORTED_WHY: Record<string, string> = {
  EARNINGS_BEAT: "자기 회사의 실적 소식이라 주가에 가장 직접 반영돼요.",
  EARNINGS_MISS: "자기 회사의 실적 소식이라 주가에 가장 직접 반영돼요.",
  SUPPLY_DEAL: "수주·공급 계약의 당사자라 앞으로의 매출 전망이 바로 바뀌어요.",
  EXPORT_CONTROL: "규제 대상 시장·고객과의 거래가 막히거나 풀리는 당사자예요.",
  TARIFF: "관세가 붙는 품목을 직접 만들고 수출하는 회사예요.",
  ACCIDENT: "사고·공급 차질이 이 회사의 생산과 출하에 바로 닿아요.",
  PRICE_UP: "제품 값이 오르면 이 회사의 판매 단가와 이익이 함께 움직여요.",
  PRICE_DOWN: "제품 값이 내리면 이 회사의 판매 단가와 이익이 함께 움직여요.",
  PRODUCTION_CUT: "감산·재고 조정은 이 회사의 생산량과 가격에 바로 닿아요.",
  CAPEX: "증설·투자의 주체라 앞으로의 생산 능력과 비용이 바뀌어요.",
  SHAREHOLDER: "자사주·배당 같은 주주환원은 이 회사 주식의 수요에 바로 닿아요.",
  AI_DEMAND: "AI·HBM 수요가 이 회사 제품 판매로 바로 이어져요.",
  POLICY: "정부 지원·규제가 이 회사의 투자와 비용에 바로 닿아요.",
  ANALYST: "증권가의 전망·목표가가 이 회사 주식에 대한 시장의 기대를 바꿔요.",
  MARKET_WRAP: "증시 흐름 기사에 이름이 오른 종목이라 시장 분위기를 그대로 받아요.",
};

const MAKER = "반도체 제조";
const SELLER_KINDS = new Set(["장비", "소재", "부품", "후공정"]);

function sells(industry: string | null): boolean {
  return industry !== MAKER && SELLER_KINDS.has(industry?.split(" ").pop() ?? "");
}

/**
 * 공급 계약 기사에 함께 나온 두 회사 중 누가 사고 누가 파는가 — 칩 제조사와 장비·소재·부품·후공정 회사가 함께 나오면 제조사가
 * 사는 쪽이다. 짝을 못 찾으면 null(공통 문장을 쓴다)
 */
function dealText(me: string, industry: string | null, others: string[], industryOf: (name: string) => string | null): string | null {
  if (industry === MAKER) {
    const seller = others.find((o) => sells(industryOf(o)));
    if (!seller) return null;
    const thing = supplied(industryOf(seller));
    const verb = thing === "패키징·테스트" ? "맡기는" : "사들이는";
    return `${seller}의 ${thing}${josa(thing, "을", "를")} ${verb} 쪽이라, 매출보다는 ${me}의 ${thing === "장비" ? "설비 투자" : "생산"} 계획이 드러나는 소식이에요.`;
  }
  if (sells(industry)) {
    const buyer = others.find((o) => industryOf(o) === MAKER);
    if (!buyer) return null;
    const thing = supplied(industry);
    return `${buyer}에 ${thing}${josa(thing, "을", "를")} 공급하는 계약을 따낸 쪽이라 앞으로의 매출 전망이 바로 바뀌어요.`;
  }
  return null;
}

interface WhyNode {
  basis: EventNode["basis"];
  companyId: string | null;
  anchorCompanyId: string | null;
  path: EventNode["path"];
  position: EventNode["position"];
  name: string;
  industry: string | null;
}

/** 경로를 언급 기업에서 이 기업까지 걸으며 단계마다 (앞 기업, 뒤 기업, 관계, 앞 기업이 관계의 source 인가)를 낸다 */
function walk(node: WhyNode) {
  const first = node.path[0];
  let cur: string | null = node.anchorCompanyId ?? (first.targetCompanyId === node.companyId ? first.sourceCompanyId : first.targetCompanyId);
  const nameOf = (id: string | null) =>
    node.path.flatMap((s) => [[s.sourceCompanyId, s.sourceName], [s.targetCompanyId, s.targetName]]).find(([i]) => i === id)?.[1] ?? "?";
  return node.path.map((s) => {
    const forward = s.sourceCompanyId === cur;
    const next = forward ? s.targetCompanyId : s.sourceCompanyId;
    const stepInfo = { from: nameOf(cur), to: nameOf(next), rel: s.relationshipType ?? "", forward };
    cur = next;
    return stepInfo;
  });
}

/**
 * "왜 이 뉴스가 이 종목에 닿을까" — 뉴스 유형, 기업 위치(기사 기업·공급사·고객사·경쟁사·협력사·지분·함께 언급), 업종, 경로로
 * 한두 문장을 풀어 쓴다. 규칙으로 만든 해석이라 '…할 수 있어요'로 말하고, 오른다·내린다는 말하지 않는다.
 * industryOf: 경로 위 다른 기업의 업종(같은 지도 노드에서 찾는다). reported: 기사에 나온 기업 이름 — 공급 계약에서 사는 쪽과
 * 파는 쪽을 가르는 데 쓴다. 경로가 없거나 위치를 모르면 null
 */
export function whyText(node: WhyNode, newsType: NewsTypeCode | null, industryOf: (name: string) => string | null,
                        reported: string[] = []): string | null {
  const me = node.name;
  const type = newsType ?? "OTHER";
  if (node.basis === "REPORTED") {
    const deal = type === "SUPPLY_DEAL" ? dealText(me, node.industry, reported.filter((r) => r !== me), industryOf) : null;
    return `${me}${josa(me, "은", "는")} 이 기사에 직접 나온 회사예요. ${deal ?? REPORTED_WHY[type] ?? "기사 내용이 이 회사에 대한 시장의 관심을 바꿔요."}`;
  }
  if (!node.path.length) return null;
  const steps = walk(node);
  const last = steps[steps.length - 1];
  const x = last.from;
  let text: string;
  if (last.rel === "SUPPLY" && !last.forward) {
    // 내가 앞 기업(x)에 공급한다
    text = `${me}${josa(me, "은", "는")} ${x}에 ${supplied(node.industry)}${josa(supplied(node.industry), "을", "를")} 공급하는 회사예요. `;
    if (DEMAND_UP.has(type)) text += `${x}${josa(x, "이", "가")} 잘되거나 투자를 늘리면 ${me}에 들어오는 주문도 늘 수 있어요.`;
    else if (DEMAND_DOWN.has(type)) text += `${x}${josa(x, "이", "가")} 생산을 줄이거나 실적이 꺾이면 ${me}의 주문도 줄 수 있어요.`;
    else if (SUPPLY_SHOCK.has(type)) text += `${x}의 생산·수출에 차질이 생기면 ${me}의 납품 일정도 함께 흔들릴 수 있어요.`;
    else text += `${x}에 대한 시장의 기대나 걱정이 공급망을 따라 ${me}에도 번지곤 해요.`;
  } else if (last.rel === "SUPPLY") {
    // 앞 기업(x)이 나에게 공급한다
    const thing = supplied(industryOf(x));
    text = `${me}${josa(me, "은", "는")} ${x}의 ${thing}${josa(thing, "을", "를")} 사 가는 고객사예요. `;
    if (type === "PRICE_UP") text += `${x} 제품 값이 오르면 ${me}의 원가 부담이 커질 수 있어요.`;
    else if (type === "PRICE_DOWN") text += `${x} 제품 값이 내리면 ${me}의 원가 부담이 줄 수 있어요.`;
    else if (DEMAND_UP.has(type)) text += `${x}의 실적·수주가 좋다는 건 고객사인 ${me}${josa(me, "이", "가")} 투자와 생산을 늘리고 있다는 신호로 읽혀요.`;
    else if (DEMAND_DOWN.has(type)) text += `${x}의 실적이 꺾이거나 생산이 줄면 고객사인 ${me}의 투자·수요가 약해졌다는 신호일 수 있어요.`;
    else if (SUPPLY_SHOCK.has(type)) text += `${x}의 공급이 흔들리면 ${me}${josa(me, "이", "가")} 쓰는 ${thing} 조달에 차질이 생길 수 있어요.`;
    else text += `${x}의 소식이 거래 관계를 따라 고객사인 ${me}에도 영향을 줄 수 있어요.`;
  } else if (last.rel === "COMPETE") {
    text = `${me}${josa(me, "은", "는")} ${x}${josa(x, "과", "와")} 같은 시장에서 경쟁하는 회사예요. ${x}의 소식이 점유율 경쟁으로는 반대로, 업황 전체로는 같은 방향으로 작용할 수 있어요.`;
  } else if (last.rel === "PARTNER") {
    text = `${me}${josa(me, "은", "는")} ${x}${josa(x, "과", "와")} 함께 사업하는 협력사예요. ${x}의 사업 변화가 공동 사업을 거쳐 ${me}에도 이어질 수 있어요.`;
  } else if (last.rel === "INVEST") {
    text = last.forward
      ? `${me}${josa(me, "은", "는")} ${x}${josa(x, "이", "가")} 지분을 가진 회사예요. 두 회사의 실적과 기업 가치가 함께 움직일 수 있어요.`
      : `${me}${josa(me, "은", "는")} ${x}의 지분을 가진 회사예요. ${x}의 가치 변화가 ${me}${josa(me, "이", "가")} 가진 지분 가치에 반영될 수 있어요.`;
  } else {
    text = `${me}${josa(me, "은", "는")} ${x}${josa(x, "과", "와")} 같은 기사에 자주 함께 나오는 회사예요. 직접 거래 근거는 없지만 시장이 함께 묶어 보는 경향이 있어요.`;
  }
  if (steps.length > 1) {
    const a = steps[0].from;
    const rel = REL_KO[steps[0].rel] ?? "관계";
    text += ` ${x}${josa(x, "은", "는")} 이 기사의 ${a}${josa(a, "과", "와")} ${rel} 관계로 이어져 있어서, 그 소식이 ${x}${josa(x, "을", "를")} 거쳐 ${me}까지 닿아요.`;
  }
  return text;
}

export const AFTER_NEWS = "이 뉴스 뒤 기사";

/**
 * 관계 근거 기사가 이 뉴스보다 뒤에 나왔는가. 관계 그래프는 전체 기간 기사로 만들어서 뉴스 뒤 기사만 근거인 관계도 있고,
 * API 는 뉴스 전 기사를 먼저 준다 — 뒤 기사는 화면에서 그렇다고 밝힌다
 */
export function afterNews(publishedAt: string, dateline: string): boolean {
  return new Date(publishedAt).getTime() > new Date(dateline).getTime();
}

const REL_KO: Record<string, string> = { SUPPLY: "공급", PARTNER: "협력", COMPETE: "경쟁", INVEST: "투자", CO_MENTIONED: "함께 언급" };
/** 방향이 있는 관계 — source 가 target 에 공급·투자한다 */
const DIRECTED = new Set(["SUPPLY", "INVEST"]);

/**
 * 영향 경로 — "SK하이닉스 ← 공급 ← 넥스틴"(넥스틴이 SK하이닉스에 공급). 기사에 나온 기업이면 null.
 * 경로는 언급 기업(anchor)에서 바깥으로 가고, 단계의 source/target 은 관계 방향이라 전파 방향과 다를 수 있다 —
 * 그래서 단계마다 "지금 기업이 아닌 쪽"을 다음 기업으로 잡고, 화살표는 관계 방향(공급·투자하는 쪽 → 받는 쪽)으로 그린다.
 * 방향이 없는 관계(협력·경쟁·함께 언급)는 "—"로 잇는다
 */
export function pathText(node: Pick<EventNode, "basis" | "path" | "companyId" | "anchorCompanyId">): string | null {
  if (node.basis === "REPORTED" || node.path.length === 0) return null;
  const first = node.path[0];
  let cur: string | null = node.anchorCompanyId ?? (first.targetCompanyId === node.companyId ? first.sourceCompanyId : first.targetCompanyId);
  const nameOf = (id: string | null) =>
    node.path.flatMap((s) => [[s.sourceCompanyId, s.sourceName], [s.targetCompanyId, s.targetName]]).find(([i]) => i === id)?.[1] ?? "?";
  let text = nameOf(cur);
  for (const s of node.path) {
    const forward = s.sourceCompanyId === cur;
    const next = forward ? s.targetCompanyId : s.sourceCompanyId;
    const rel = REL_KO[s.relationshipType ?? ""] ?? "관계";
    if (!DIRECTED.has(s.relationshipType ?? "")) text += ` — ${rel} — `;
    else text += forward ? ` → ${rel} → ` : ` ← ${rel} ← `;
    text += nameOf(next);
    cur = next;
  }
  return text;
}
