/**
 * 투자자 화면 문구와 숫자 표기 — 딱지·과거 사례·지표·적중률을 어떤 말로 보여 줄지 한곳에서 정한다.
 * 딱지는 과거 사례 통계라서 언제나 "과거 …"로 쓰고, 예측처럼 읽히는 말("오른다", "추천")은 쓰지 않는다.
 * import 가 타입뿐인 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/investCopy.test.ts).
 */
import type {
  BacktestRow,
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

/** 피드 요약 — "하락 우세 2 · 상승 우세 1 · 보류 5" (0 인 항목은 뺀다) */
export function countsText(counts: SignalCount | null | undefined): string {
  if (!counts) return "";
  const parts = [
    counts.down ? `하락 우세 ${counts.down}` : null,
    counts.up ? `상승 우세 ${counts.up}` : null,
    counts.hold ? `보류 ${counts.hold}` : null,
  ].filter(Boolean);
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

/** 적중률 한 줄과 기준선 비교. 딱지가 하나도 없으면 null */
export function accuracyText(row: BacktestRow | null | undefined): { line: string; beats: boolean } | null {
  if (!row || !row.labeledCount || row.hitRate == null) return null;
  const base = row.baselineRate;
  const line = `딱지 ${row.labeledCount.toLocaleString("ko-KR")}건 중 ${fmtPct(row.hitRate, 1, false)} 적중` +
    (base != null ? ` · 무조건 많은 쪽으로 답할 때 ${fmtPct(base, 1, false)}` : "");
  return { line, beats: base == null ? false : row.hitRate > base };
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

/** 적중률 공개 띠의 변동폭 검증 한 줄. 검증 행이 없으면 null */
export function moveCheckText(rows: MoveBacktestRow[], horizon: Horizon): { line: string; holds: boolean } | null {
  const small = rows.find((r) => r.horizon === horizon && r.grade === "SMALL");
  const large = rows.find((r) => r.horizon === horizon && r.grade === "LARGE");
  if (!small || !large || small.realizedMove == null || large.realizedMove == null) return null;
  const holds = gradesHold(rows, horizon);
  const line = `'변동 큼' 종목 실제 ±${fmtPct(large.realizedMove, 1, false)} · '변동 작음' 종목 실제 ±${fmtPct(small.realizedMove, 1, false)}`;
  return { line, holds };
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

/**
 * 영향 경로 — "삼성전자 → 공급 → 한미반도체". 기사에 나온 기업이면 null.
 * 경로는 언급 기업(anchor)에서 바깥으로 가고, 단계의 source/target 은 관계 방향이라 전파 방향과 다를 수 있다 —
 * 그래서 단계마다 "지금 기업이 아닌 쪽"을 다음 기업으로 잡는다
 */
export function pathText(node: Pick<EventNode, "basis" | "path" | "companyId" | "anchorCompanyId">): string | null {
  if (node.basis === "REPORTED" || node.path.length === 0) return null;
  const first = node.path[0];
  let cur: string | null = node.anchorCompanyId ?? (first.targetCompanyId === node.companyId ? first.sourceCompanyId : first.targetCompanyId);
  const nameOf = (id: string | null) =>
    node.path.flatMap((s) => [[s.sourceCompanyId, s.sourceName], [s.targetCompanyId, s.targetName]]).find(([i]) => i === id)?.[1] ?? "?";
  const parts = [nameOf(cur)];
  for (const s of node.path) {
    const next = s.sourceCompanyId === cur ? s.targetCompanyId : s.sourceCompanyId;
    parts.push(REL_KO[s.relationshipType ?? ""] ?? "관계", nameOf(next));
    cur = next;
  }
  return parts.join(" → ");
}
