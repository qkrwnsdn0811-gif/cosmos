import { spreadEdgeAlpha } from "@/lib/spread";
import { useGalaxy } from "@/store/galaxy";

/**
 * 펼치기 전이 상태 — SpreadDriver 가 쓰고 간선·화살촉·글자·링·별가루가 읽는다 (altitude.ts 의 altitudeState 와 같은 역할).
 * 좌표의 단일 소스는 live 버퍼(morph.ts)이고, 여기는 "지금 전이 중인가" 와 "live 가 어느 t 를 반영하는가" 만 둔다.
 *   t        전이 시작으로부터의 초
 *   applied  live 가 지금 반영하고 있는 슬라이더 값 (NaN = 아직 한 번도 안 씀)
 *   version  목표가 바뀐 횟수
 */
export const spreadState = { version: 0, animating: false, t: 0, applied: Number.NaN };

/* ---- 전이 타임라인 (초) — 고도(ALT_*)보다 짧다: 슬라이더를 끄는 동안 여러 번 이어 붙기 때문 ---- */
export const SP_FADE = 0.15;
export const SP_MOVE = 0.6;
export const SP_REVEAL = 0.3;
export const SP_TOTAL = SP_FADE + SP_MOVE + SP_REVEAL;

/**
 * 간선 게이트 — altitudeGate 와 같은 의미라 소비자는 세 게이트(morph·altitude·spread)의 최솟값을 쓴다.
 * 이동 구간에서는 fade·reveal 을 0 으로 두어 어긋난 간선이 한 프레임도 보이지 않게 한다.
 */
export function spreadGate(): { fade: number; reveal: number } {
  if (!spreadState.animating) return { fade: 1, reveal: 1 };
  const t = spreadState.t;
  if (t < SP_FADE) return { fade: 1 - t / SP_FADE, reveal: 1 };
  const move = t - SP_FADE;
  if (move < SP_MOVE) return { fade: 0, reveal: 0 };
  return { fade: 1, reveal: Math.min(1, (move - SP_MOVE) / SP_REVEAL) };
}

/**
 * 정렬 단계에서 간선·화살촉·관계 글자·기준 링·별가루가 남는 비율 (0~1).
 * 전이 게이트와 달리 슬라이더 값(스토어)을 바로 따른다 — 손이 움직이는 대로 즉시 흐려진다.
 * 은하 뷰가 아니면(기업 중심 뷰) 펼치기는 적용되지 않으므로 1 이다.
 */
export function spreadDim() {
  const s = useGalaxy.getState();
  if (s.phase === "system") return 1;
  return spreadEdgeAlpha(s.spread);
}
