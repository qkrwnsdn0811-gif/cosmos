import type { AltitudeWindow } from "@/api/types";
import type { SceneNode } from "@/lib/graph";

/**
 * 주가 고도 — 은하 뷰에서 기업의 기간 등락률을 행성 높이로 읽는다 (위 = 상승, 아래 = 하락).
 * 좌표의 단일 소스는 여전히 live 버퍼(morph.ts)이고, 여기는 "목표 높이" 와 "전이 타임라인" 만 정의한다.
 * node.pos 는 불변이라 고도는 언제나 레이아웃 y 에 더하는 오프셋이다 — 고도를 꺼도 원래 원반으로 정확히 돌아온다.
 */

/** 이 등락률(%)에서 높이가 포화한다 — 급등락 한 종목이 원반을 통째로 끌고 올라가지 않게 자른다 */
export const ALT_LIMIT = 15;
/** 포화 지점의 높이(월드). 원반 두께(±약 8)보다 확실히 커야 상승·하락이 한눈에 갈린다 */
export const ALT_SPAN = 40;

/** 등락률(%) → 높이 오프셋. 값이 없는 기업(null·미제공)은 평면에 남는다 */
export function altitudeY(pct: number | null | undefined) {
  if (pct == null || !Number.isFinite(pct)) return 0;
  return (Math.max(-ALT_LIMIT, Math.min(ALT_LIMIT, pct)) / ALT_LIMIT) * ALT_SPAN;
}

/** 노드의 선택 기간 등락률(%) — 기업 중심 뷰 노드는 필드 자체가 없어 null 이다 */
export function pctOf(node: SceneNode, w: AltitudeWindow) {
  return node.priceChange?.[w] ?? null;
}

/** 노드가 가야 할 y — 레이아웃 높이 + 고도 오프셋 */
export function targetY(node: SceneNode, w: AltitudeWindow) {
  return node.pos[1] + altitudeY(pctOf(node, w));
}

/* ---- 전이 타임라인 (초) ----------------------------------------------- */
/**
 * 간선을 매 프레임 다시 굽지 않으려고 전이를 세 구간으로 나눈다.
 *   0 ~ FADE      간선 페이드 아웃 (행성은 아직 그대로)
 *   FADE ~ +MOVE  행성 y 만 easeInOut 이동 — 간선은 숨어 있으므로 재생성이 필요 없다
 *   이동 끝        인접 간선 1회 재생성 (dragState.dirty + version++)
 *   ~ +REVEAL     간선 리빌 (안쪽에서 바깥으로 자란다)
 */
export const ALT_FADE = 0.25;
export const ALT_MOVE = 0.8;
export const ALT_REVEAL = 0.4;
export const ALT_TOTAL = ALT_FADE + ALT_MOVE + ALT_REVEAL;

/**
 * 전이 상태 — AltitudeDriver 가 쓰고 간선·화살촉·헤일로가 읽는다 (morphState 와 같은 역할).
 * t 는 전이 시작으로부터의 초, version 은 목표 높이가 바뀐 횟수다.
 */
export const altitudeState = { version: 0, animating: false, t: 0 };

/**
 * 지금의 간선 게이트 — morphState.fade/reveal 과 같은 의미라 소비자는 둘의 최솟값을 쓴다.
 * 이동 구간에서 fade·reveal 을 모두 0 으로 두어, 행성이 움직이는 동안 어긋난 간선이 한 프레임도 보이지 않게 한다.
 */
export function altitudeGate(): { fade: number; reveal: number } {
  if (!altitudeState.animating) return { fade: 1, reveal: 1 };
  const t = altitudeState.t;
  if (t < ALT_FADE) return { fade: 1 - t / ALT_FADE, reveal: 1 };
  const move = t - ALT_FADE;
  if (move < ALT_MOVE) return { fade: 0, reveal: 0 };
  return { fade: 1, reveal: Math.min(1, (move - ALT_MOVE) / ALT_REVEAL) };
}
