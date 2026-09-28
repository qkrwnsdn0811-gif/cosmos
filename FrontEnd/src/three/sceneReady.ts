import { useSyncExternalStore } from "react";

/**
 * 씬 준비 신호 — 은하 모델이 처음 마운트된 뒤 셰이더 컴파일·텍스처 업로드가 끝나 첫 프레임이 매끄럽게 나갈 수 있는 시점.
 * Director(Scene.tsx)의 워밍업이 세우고, GalaxyPage 의 로딩 오버레이와 Tour 의 자동 시작이 읽는다.
 * 같은 씬이 살아 있는 동안은 true 로 남는다 — 컴파일된 프로그램이 렌더러 캐시에 있어 다음 모델(워프 목적지·필터)은 같은 멈춤을 겪지 않는다.
 * 씬이 언마운트되면(다른 탭으로 이동) R3F 가 WebGL 컨텍스트를 버려 프로그램도 사라지므로 resetSceneReady 로 되돌린다 — 은하 탭에 돌아와 새로 마운트되면 다시 워밍업한다.
 * 스토어가 아니라 모듈 변수인 이유: R3F 프레임 루프(useFrame)에서 세우는 값이라 리렌더 없이 써야 하고, 읽는 쪽만 구독하면 된다.
 */
let ready = false;
const listeners = new Set<() => void>();

export function markSceneReady() {
  if (ready) return;
  ready = true;
  listeners.forEach((fn) => fn());
}

export function resetSceneReady() {
  if (!ready) return;
  ready = false;
  listeners.forEach((fn) => fn());
}

export function isSceneReady() {
  return ready;
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => void listeners.delete(fn);
}

export function useSceneReady() {
  return useSyncExternalStore(subscribe, isSceneReady, isSceneReady);
}
