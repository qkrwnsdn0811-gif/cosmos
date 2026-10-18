/**
 * 은하 간선 고르기 — 빅카인즈 "함께 언급"(같은 문장 동시언급)은 관계의 근거가 아니라서,
 * 같은 두 기업 사이에 근거 관계(공급·협력·경쟁·투자)가 있으면 점수와 관계없이 그쪽을 그린다.
 * import 가 없는 순수 함수라 node --test 로 바로 검증한다 (FrontEnd/tests/edgePick.test.ts).
 */
export const COMENTION = "CO_MENTIONED";
/** 기본 상태에서 함께 언급 간선에 곱하는 흐림 계수 */
export const COMENTION_DIM = 0.45;

export function isComention(type: string): boolean {
  return type === COMENTION;
}

export interface PairEdge {
  sourceCompanyId: string;
  targetCompanyId: string;
  relationshipType: string;
  score: number;
}

/** 방향과 관계없는 기업쌍마다 하나. 결과는 각 쌍이 입력에 처음 나온 순서 */
export function strongestPerPair<T extends PairEdge>(edges: T[]): T[] {
  const best = new Map<string, T>();
  for (const e of edges) {
    const a = e.sourceCompanyId;
    const b = e.targetCompanyId;
    const key = a < b ? `${a}:${b}` : `${b}:${a}`;
    const cur = best.get(key);
    if (!cur) {
      best.set(key, e);
      continue;
    }
    const curCo = isComention(cur.relationshipType);
    const newCo = isComention(e.relationshipType);
    if ((curCo && !newCo) || (curCo === newCo && e.score > cur.score)) best.set(key, e);
  }
  return [...best.values()];
}
