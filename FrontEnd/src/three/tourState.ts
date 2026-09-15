/**
 * 첫 방문 투어의 스포트라이트 앵커 — 씬 안의 대상(원반 전체·간선 하나·행성 하나)을 화면 좌표로 옮겨 놓는 공유 버퍼.
 * TourAnchor(R3F, 프레임마다 씀) 와 Tour(DOM, rAF 로 읽음) 가 리렌더 없이 만나는 자리라 스토어가 아니라 모듈 변수다.
 * 좌표는 캔버스 좌상단 기준 CSS px, r 은 스포트라이트 반지름(px).
 */
export type TourTarget = { kind: "disc" } | { kind: "edge"; id: string } | { kind: "node"; id: string } | null;

export const tourAnchor = {
  target: null as TourTarget,
  x: 0,
  y: 0,
  r: 0,
  /** 대상이 모델에 있고 카메라 앞에 있는지 — 아니면 Tour 가 구멍 없는 딤으로 그린다 */
  visible: false,
};
