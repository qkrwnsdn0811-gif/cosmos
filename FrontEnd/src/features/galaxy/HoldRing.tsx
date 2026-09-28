import type { CSSProperties } from "react";
import { HOLD_MS, useGalaxy } from "@/store/galaxy";

const R = 25.5;
const CIRC = 2 * Math.PI * R;

/**
 * 길게 누르기 진행 표시 — 모바일에서 행성을 꾹 누르면 손가락 자리에 원이 차오르고, 다 차면 그 기업의 관계망으로 워프한다(CompanyNodes).
 * 상태는 store.hold(누른 자리·기업) 하나뿐이고, 손을 떼거나 끌기 시작하면 사라진다. 그리기는 CSS 애니메이션(hold-fill, HOLD_MS)이 맡는다
 */
export default function HoldRing() {
  const hold = useGalaxy((s) => s.hold);
  if (!hold) return null;
  return (
    <div className="hold-ring" style={{ left: hold.x, top: hold.y, "--hold-ms": `${HOLD_MS}ms` } as CSSProperties} aria-hidden="true">
      <svg viewBox="0 0 56 56">
        <circle className="track" cx="28" cy="28" r={R} />
        <circle className="fill" cx="28" cy="28" r={R} style={{ strokeDasharray: CIRC, strokeDashoffset: CIRC }} />
      </svg>
    </div>
  );
}
