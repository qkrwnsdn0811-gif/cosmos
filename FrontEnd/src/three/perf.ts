let lowEnd: boolean | null = null;
/** 논리 코어 4 이하 = 통합 GPU 노트북으로 보고 파티클 수를 줄인다 (첫 호출 때 한 번만 읽는다) */
export function isLowEnd() {
  if (lowEnd === null) lowEnd = typeof navigator !== "undefined" && (navigator.hardwareConcurrency ?? 8) <= 4;
  return lowEnd;
}
