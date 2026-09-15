/** 씬 키 입력 헬퍼 — OrbitKeys 가 키를 읽고 HUD·입력 상자·모달에 양보하는 규칙 */

/** 일부 환경(자동화 도구·가상 키보드)은 e.code 를 비워 보내므로 e.key 로 보완한다 */
export function codeOf(e: KeyboardEvent) {
  if (e.code) return e.code;
  const k = e.key;
  if (k === " ") return "Space";
  if (k === "Shift") return "ShiftLeft";
  if (k.length === 1) {
    const u = k.toUpperCase();
    if (u >= "A" && u <= "Z") return `Key${u}`;
  }
  return k;
}

/** 입력 상자·textarea·select·contentEditable 에 포커스가 있으면 씬이 키를 가로채지 않는다 */
export function isTyping() {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/**
 * 포커스된 요소를 감싸는 상자 중 그 축으로 실제 스크롤이 남아 있는 것이 있는지 — 있으면 방향키는 그 스크롤에 양보한다.
 * 우측 인텔리전스 패널(.panel-body)의 뉴스 목록 안 버튼·링크에 포커스가 있을 때 ↑↓ 가 목록을 넘기던 동작을 지키기 위한 것.
 * 캔버스는 포커스를 받지 않으므로 씬을 클릭한 뒤에는 activeElement 가 body 라 항상 false 다.
 */
export function focusScrolls(axis: "x" | "y") {
  let el = document.activeElement as HTMLElement | null;
  if (!el || el === document.body) return false;
  for (; el && el !== document.body; el = el.parentElement) {
    const cs = getComputedStyle(el);
    const overflow = axis === "y" ? cs.overflowY : cs.overflowX;
    if (overflow !== "auto" && overflow !== "scroll") continue;
    if (axis === "y" ? el.scrollHeight > el.clientHeight : el.scrollWidth > el.clientWidth) return true;
  }
  return false;
}

/** aria-modal 대화상자(로그인 등)가 떠 있는지 — 모달 안의 버튼에 포커스가 있을 때 씬이 키를 가로채지 않게 */
export function modalOpen() {
  return document.querySelector('[aria-modal="true"]') !== null;
}
