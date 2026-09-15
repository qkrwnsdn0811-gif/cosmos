import type { ReactElement } from "react";
import { READBAR } from "@/lib/guide";
import { useGalaxy } from "@/store/galaxy";
import { EdgeGlyph } from "@/components/ui";
import { LAB } from "@/three/toon";
import "./hud-guide.css";

/** 자리 글리프 — 가까운 점 두 개 (거리=관계 강도) */
function PlaceGlyph() {
  return (
    <svg className="readbar-glyph" width="16" height="10" viewBox="0 0 16 10" aria-hidden>
      <circle cx="5" cy="5" r="3" fill="currentColor" />
      <circle cx="12" cy="5" r="3" fill="currentColor" />
    </svg>
  );
}
/** 굵기 글리프 — 굵기가 다른 두 선 (굵기=점수) */
function WidthGlyph() {
  return (
    <svg className="readbar-glyph" width="18" height="10" viewBox="0 0 18 10" aria-hidden>
      <line x1="1" y1="3" x2="17" y2="3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <line x1="1" y1="8" x2="17" y2="8" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" />
    </svg>
  );
}
/** 색 글리프 — 긍정·부정·중립 세 점 */
function ColorGlyph() {
  return (
    <span className="readbar-dots">
      <i style={{ background: LAB.beamPositive }} />
      <i style={{ background: LAB.beamNegative }} />
      <i style={{ background: LAB.beamNeutral }} />
    </span>
  );
}
/** 관계 종류 글자 글리프 — 알약 안 예시 라벨 */
function LabelGlyph() {
  return <span className="readbar-pill">공급</span>;
}

const GLYPHS: Record<string, () => ReactElement> = {
  place: PlaceGlyph,
  width: WidthGlyph,
  color: ColorGlyph,
  dash: () => <EdgeGlyph directed={false} color={LAB.beamNeutral} />,
  label: LabelGlyph,
};
/** 900px 미만에서는 자리·색 두 항목만 남긴다 */
const NARROW_KEEP = new Set(["place", "color"]);

/** 하단 중앙 상시 "읽는 법" 1행 — 씬 인코딩(자리·굵기·색·점선·글자)을 늘 보이는 곳에 요약한다 (기획서 P0-1) */
export default function ReadBar() {
  const legendOpen = useGalaxy((s) => s.legendOpen);
  const toggleLegend = useGalaxy((s) => s.toggleLegend);
  return (
    <div className="hud hud-readbar">
      <button type="button" className="readbar" aria-expanded={legendOpen} title={legendOpen ? READBAR.collapse : READBAR.expand} onClick={toggleLegend}>
        {READBAR.items.map((it) => {
          const Glyph = GLYPHS[it.key];
          return (
            <span key={it.key} className={`readbar-item ${NARROW_KEEP.has(it.key) ? "" : "readbar-hide-narrow"}`}>
              <Glyph />
              {it.text}
            </span>
          );
        })}
      </button>
    </div>
  );
}
