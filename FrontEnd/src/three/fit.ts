/**
 * 소형 3D 지도(MiniScene)의 첫 카메라 거리. 궤도(반지름 radius)는 가로로 반지름의 FIT_MARGIN 배까지, 주가 반응으로
 * 뜨거나 가라앉은 행성(높이 ±height)과 이름표는 세로로 들어오게 하고, 처음부터 너무 확대돼 보이지 않게 최소 거리를 둔다
 * (2026-10-19 "처음에 들어갈 때 너무 확대되지 않게"). import 가 없는 순수 함수라 node --test 로 검증한다.
 */
export const FIT_MARGIN = 1.5;
/** 궤도 반지름 대비 최소 카메라 거리 */
export const MIN_DISTANCE_RATIO = 2.4;
/** 행성 위 이름표가 차지하는 높이(월드) */
const LABEL_ROOM = 12;
/** 비스듬히 내려다볼 때 원반이 세로로 차지하는 비율(반지름 대비) */
const DISC_DEPTH = 0.55;

export function fitDistance(radius: number, height: number, fovDeg: number, aspect: number): number {
  const v = (fovDeg * Math.PI) / 360;
  const h = Math.atan(Math.tan(v) * Math.max(0.1, aspect));
  const horizontal = (radius * FIT_MARGIN) / Math.tan(h);
  const vertical = (radius * DISC_DEPTH + height + LABEL_ROOM) / Math.tan(v);
  return Math.max(horizontal, vertical, radius * MIN_DISTANCE_RATIO);
}
