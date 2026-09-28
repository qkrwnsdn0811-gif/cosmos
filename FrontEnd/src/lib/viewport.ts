import { useEffect, useState } from "react";

/**
 * 모바일 취급 기준 — CSS 의 `@media (max-width: 900px)` 규칙들(헤더·HUD 자리·도크 줄바꿈)과 같은 값이다.
 * JS 분기(헤더 ☰ 서랍 · 도크 기본 접힘 · Special Link 접힘)와 CSS 가 같은 폭에서 켜져야 두 층이 어긋나지 않는다.
 */
export const NARROW_BREAKPOINT = 900;
const QUERY = `(max-width: ${NARROW_BREAKPOINT}px)`;

/** 지금 뷰포트가 좁은지 한 번 읽는다 — 스토어 초기값처럼 훅을 쓸 수 없는 자리용. matchMedia 가 없는 환경(테스트)은 넓은 화면으로 본다 */
export function isNarrowViewport(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(QUERY).matches : false;
}

/** 좁은 뷰포트 여부를 구독한다 — 창 크기 변경·기기 회전에 따라 다시 렌더된다 */
export function useNarrowViewport(): boolean {
  const [narrow, setNarrow] = useState(isNarrowViewport);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(QUERY);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return narrow;
}
