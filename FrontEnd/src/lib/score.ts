import type { GraphEdge, RelationshipDetail } from "@/api/types";

/**
 * 관계 표시 점수 — 뉴스·공시 구성 점수를 화면에서만 섞는다 (명세 graph '관계 점수 표시 규칙').
 *
 *  - 기본 비율(0.5)이면 서버가 계산한 공통 점수 score 를 그대로 쓴다
 *    (명세: "탭 세션 종료 후 다시 접속하면 기본 score 를 사용한다" — 기본 상태의 점수는 서버가 정한다)
 *  - 비율을 조절하면  newsScore × newsWeight + disclosureScore × (1 - newsWeight)
 *  - 한쪽만 있으면 가중치와 무관하게 있는 쪽을 그대로 쓴다
 *  - 둘 다 null 이면 표시할 점수가 없다 → null (호출 측이 그 관계를 화면에서 뺀다)
 *  - 서버가 아직 구성 점수를 내려주지 않으면(필드 자체가 없음) 공통 점수 score 를 그대로 쓴다
 */
/** 슬라이더 초기값 — 명세 '관계 점수 표시 규칙'의 newsWeight 0.5 */
export const DEFAULT_NEWS_WEIGHT = 0.5;

const round1 = (v: number) => Math.round(v * 10) / 10;

export interface ScoreParts {
  score: number;
  newsScore?: number | null;
  disclosureScore?: number | null;
}

export function blendScore(parts: ScoreParts, newsWeight: number): number | null {
  const { newsScore: n, disclosureScore: d } = parts;
  // 구성 점수를 아예 받지 못한 응답 — 공통 점수만 믿는다
  if (n === undefined && d === undefined) return parts.score;
  if (n == null && d == null) return null;
  if (n == null) return d!;
  if (d == null) return n;
  // 기본 비율에서는 프론트가 다시 계산하지 않는다 — 서버의 공통 점수가 곧 뉴스 50 · 공시 50 이다
  if (newsWeight === DEFAULT_NEWS_WEIGHT) return parts.score;
  return round1(n * newsWeight + d * (1 - newsWeight));
}

/** 표시 점수를 score 자리에 채운 간선 목록 — 점수를 만들 수 없는 관계는 빼고 돌려준다 */
export function weightedEdges<E extends GraphEdge>(edges: E[], newsWeight: number): E[] {
  const out: E[] = [];
  for (const e of edges) {
    const s = blendScore(e, newsWeight);
    if (s === null) continue;
    out.push(s === e.score ? e : { ...e, score: s });
  }
  return out;
}

/**
 * 지금 점수가 무엇으로 만들어졌는지 한 줄 — 상세 패널이 기준 시각 옆에 붙인다.
 * 기본 비율(50:50)이고 양쪽 근거가 다 있으면 덧붙일 말이 없다(null).
 */
export function scoreBasisLabel(parts: ScoreParts, newsWeight: number): string | null {
  const { newsScore: n, disclosureScore: d } = parts;
  if (n == null && d == null) return null;
  if (n == null) return "공시 근거만 반영";
  if (d == null) return "뉴스 근거만 반영";
  if (newsWeight === DEFAULT_NEWS_WEIGHT) return null;
  const news = Math.round(newsWeight * 100);
  return `뉴스 ${news} : 공시 ${100 - news} 반영`;
}

/** 관계 상세도 같은 규칙으로 섞는다. 구성 점수가 둘 다 없으면 공통 점수를 그대로 보여 준다 */
export function weightedRelationship(detail: RelationshipDetail, newsWeight: number): RelationshipDetail {
  const s = blendScore(detail, newsWeight);
  return s === null || s === detail.score ? detail : { ...detail, score: s };
}
