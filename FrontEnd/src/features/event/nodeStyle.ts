/**
 * 지도 노드 모양과 근거 패널 문구 — 영향 모델 결과(방향·세기·신뢰도)를 어떻게 보여줄지 한곳에서 정한다.
 * 판단 보류(HOLD)가 가장 우선이고, 모델이 돌지 않은 노드(해외 상장 등)는 근거·추정 색을 그대로 쓴다.
 * import 가 없는 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/nodeStyle.test.ts).
 */
export interface NodeStyleInput {
  basis: "REPORTED" | "INFERRED";
  status: "OK" | "HOLD";
  score: number | null;
  impactDirection: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null;
  impactMagnitude: number | null;
  confidence: number | null;
}

export interface NodeStyle {
  fill: string;
  fillOpacity: number;
  /** 속 빈 원(방향 보류)의 테두리 색. 그 밖에는 null */
  stroke: string | null;
  radius: number;
  /** 노드 툴팁·aria-label 끝에 붙는 말 */
  impactLabel: string | null;
}

export const IMPACT_DISCLAIMER = "과거 비슷한 기사에 시장이 어떻게 반응했는지로 학습한 추정치이며, 주가 예측이 아닙니다.";

const DIRECTION = {
  POSITIVE: { fill: "var(--pos)", ko: "상승" },
  NEGATIVE: { fill: "var(--neg)", ko: "하락" },
  NEUTRAL: { fill: "var(--impact-neutral)", ko: "중립" },
} as const;

/** 영향 모델이 이 노드를 평가했는가 — 판단 보류 노드에 남은 옛 값은 무시한다 */
export function modelRan(n: NodeStyleInput): boolean {
  return n.status === "OK" && n.confidence !== null;
}

function basisColor(n: NodeStyleInput) {
  return n.basis === "REPORTED" ? "var(--primary)" : "var(--accent)";
}

function scoreRadius(n: NodeStyleInput) {
  const s = n.score ?? 0.5;
  return n.basis === "REPORTED" ? 10 + 8 * s : 8 + 10 * s;
}

export function nodeStyle(n: NodeStyleInput): NodeStyle {
  if (n.status === "HOLD") {
    return { fill: "var(--text-4)", fillOpacity: 0.6, stroke: null, radius: scoreRadius(n), impactLabel: null };
  }
  if (!modelRan(n)) {
    return { fill: basisColor(n), fillOpacity: 1, stroke: null, radius: scoreRadius(n), impactLabel: null };
  }
  const radius = 9 + 12 * (n.impactMagnitude ?? 0);
  if (n.impactDirection === null) {
    return { fill: "var(--panel-solid)", fillOpacity: 1, stroke: basisColor(n), radius, impactLabel: "방향 보류" };
  }
  const d = DIRECTION[n.impactDirection];
  return { fill: d.fill, fillOpacity: 1, stroke: null, radius, impactLabel: `${d.ko} 추정` };
}

/** 근거 패널 한 줄. 모델이 돌지 않았으면 null */
export function impactLine(n: NodeStyleInput): string | null {
  if (!modelRan(n)) return null;
  const confidence = `신뢰도 ${Math.round((n.confidence ?? 0) * 100)}%`;
  if (n.impactDirection === null) return `영향 방향 보류 · ${confidence}`;
  return `모델 추정: ${DIRECTION[n.impactDirection].ko} · ${confidence} · 세기 ${(n.impactMagnitude ?? 0).toFixed(2)}`;
}
