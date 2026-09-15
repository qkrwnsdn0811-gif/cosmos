/**
 * 노드 애니메이션 상태 — 기업 id 로 찾는 모듈 캐시.
 * 컴포넌트 안에 두면 모델(은하 ↔ 기업 중심 뷰)이 바뀔 때마다 0 에서 다시 시작해 같은 기업이 매번 팝인한다.
 * 모듈에 두면 뷰가 바뀌어도 같은 기업은 같은 상태를 이어받고, 워프 모프 중 마운트도 상태를 보존할 수 있다.
 */
export interface NodeAnim {
  /** 행성 그룹 스케일 (팝인은 0.001 에서 시작) */
  scale: number;
  /** 행성 밝기 배율 */
  bright: number;
  /** 정면 배지 알파 */
  tagA: number;
  /** 이름표 칩 알파 */
  chipA: number;
  /** 칩의 이름 부분 펼침 (0 = 로고 원만, 1 = 기업명까지) */
  nameOn: number;
}

const ANIM = new Map<string, NodeAnim>();

/** 기업 id 의 애니메이션 상태. 처음 보는 id 는 팝인 직전 상태로 만든다 */
export function animFor(id: string): NodeAnim {
  let a = ANIM.get(id);
  if (!a) {
    a = { scale: 0.001, bright: 1, tagA: 0, chipA: 0, nameOn: 1 };
    ANIM.set(id, a);
  }
  return a;
}
