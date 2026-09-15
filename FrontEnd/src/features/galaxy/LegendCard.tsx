import { useState } from "react";
import type { AltitudeWindow } from "@/api/types";
import { EdgeGlyph, Icon } from "@/components/ui";
import { LEGEND_UI, READBAR } from "@/lib/guide";
import { RELATIONSHIP_ORDER, relationshipMeta } from "@/lib/meta";
import { useGalaxy } from "@/store/galaxy";
import { DEFAULT_NEWS_WEIGHT, pct, useWeight } from "@/store/weight";
import { LAB } from "@/three/toon";
import "./hud-guide.css";

/** 주가 고도의 기준 기간 — 은하 뷰 행성 높이가 이 기간의 등락률을 쓴다 */
const ALTITUDE_WINDOWS: AltitudeWindow[] = ["1D", "1M", "3M"];
/** 영향 색 범례 — toon.beamColorFor 와 같은 색·같은 순서라야 범례가 씬을 설명한다 */
const IMPACT_COLOR: Record<string, string> = { POSITIVE: LAB.beamPositive, NEGATIVE: LAB.beamNegative, NEUTRAL: LAB.beamNeutral };

/**
 * 우하단 필터·범례 카드 (P0-10). 종류(선 위 글자로 구분)와 영향(색으로 구분)을 소제목으로 갈라
 * "색이 관계 종류가 아니라 영향 방향" 이라는 오독을 막는다. 첫 화면 조작을 줄이려 상위만·이름표·관계 글자는 "고급" 뒤로 접는다.
 */
export default function LegendCard() {
  const [advOpen, setAdvOpen] = useState(false);
  const focusId = useGalaxy((s) => s.focusId);
  const legendOpen = useGalaxy((s) => s.legendOpen);
  const toggleLegend = useGalaxy((s) => s.toggleLegend);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const toggleType = useGalaxy((s) => s.toggleType);
  const minScore = useGalaxy((s) => s.minScore);
  const setMinScore = useGalaxy((s) => s.setMinScore);
  const edgeBudget = useGalaxy((s) => s.edgeBudget);
  const toggleEdgeBudget = useGalaxy((s) => s.toggleEdgeBudget);
  const showLabels = useGalaxy((s) => s.showLabels);
  const toggleLabels = useGalaxy((s) => s.toggleLabels);
  const showEdgeLabels = useGalaxy((s) => s.showEdgeLabels);
  const toggleEdgeLabels = useGalaxy((s) => s.toggleEdgeLabels);
  const altitudeOn = useGalaxy((s) => s.altitudeOn);
  const setAltitude = useGalaxy((s) => s.setAltitude);
  const altitudeWindow = useGalaxy((s) => s.altitudeWindow);
  const setAltitudeWindow = useGalaxy((s) => s.setAltitudeWindow);
  const newsWeight = useWeight((s) => s.newsWeight);
  const setNewsWeight = useWeight((s) => s.setNewsWeight);
  const resetWeight = useWeight((s) => s.reset);
  const newsPct = pct(newsWeight);

  // 접힘 상태는 아무것도 그리지 않는다 — 펼치는 진입점은 하단 중앙 '읽는 법' 바(ReadBar) 하나다. 꼬리표까지 두면 같은 스위치가 둘이 된다
  if (!legendOpen) return null;

  return (
    <div className="hud hud-legend">
      <div className="card legend-card">
        <div className="row between legend-head">
          <span className="kick">{LEGEND_UI.kicker}</span>
          <div className="row legend-actions">
            <button type="button" className="toggle" onClick={toggleLegend} aria-label={READBAR.collapse} title={READBAR.collapse}>
              <Icon.Close />
            </button>
          </div>
        </div>

        {/* 종류 — 색이 아니라 선 위 글자·호버 링으로 구분한다는 것을 소제목으로 못박는다 */}
        <div className="legend-sec">
          <span className="legend-sub">{LEGEND_UI.typesTitle}</span>
          <div className="legend-types legend-types-plain">
            <div className="row wrap" style={{ gap: 8 }}>
              {RELATIONSHIP_ORDER.map((t) => {
                const m = relationshipMeta(t);
                const on = activeTypes.has(t);
                return (
                  <button key={t} type="button" className={`chip chip-sm ${on ? "on" : ""}`} onClick={() => toggleType(t)} title={m.description} style={{ opacity: on ? 1 : 0.55 }}>
                    <EdgeGlyph directed={m.directed} color={LAB.beamNeutral} />
                    {m.label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="meta legend-hint">{LEGEND_UI.typesHint}</div>
        </div>

        {/* 영향 — 선 색은 관계 종류가 아니라 이 방향이다 */}
        <div className="legend-sec">
          <span className="legend-sub">{LEGEND_UI.impactTitle}</span>
          <div className="meta legend-impact">
            {LEGEND_UI.impact.map((it) => (
              <span key={it.key}>
                <i className="dot" style={{ background: IMPACT_COLOR[it.key] }} />
                {it.label}
              </span>
            ))}
          </div>
          <div className="meta legend-hint">{LEGEND_UI.widthHint}</div>
        </div>

        {/* 뉴스·공시 비율 — 서버에 저장하지 않고 이 탭에서만 유지되는 표시 설정 (store/weight) */}
        <div className="legend-sec">
          <div className="row between">
            <span className="legend-sub">{LEGEND_UI.mixTitle}</span>
            {newsWeight !== DEFAULT_NEWS_WEIGHT && (
              <button type="button" className="toggle" onClick={resetWeight} title={LEGEND_UI.mixScope}>
                {LEGEND_UI.mixReset}
              </button>
            )}
          </div>
          <div className="row-ctl">
            <span className="lab">
              뉴스 <b className="num">{newsPct}</b>
            </span>
            <input type="range" min={0} max={100} step={5} value={newsPct} onChange={(e) => setNewsWeight(Number(e.target.value) / 100)} aria-label={LEGEND_UI.mixTitle} />
            <span className="lab">
              공시 <b className="num">{100 - newsPct}</b>
            </span>
          </div>
          <div className="meta legend-hint">{LEGEND_UI.mixHint}</div>
        </div>

        <div className="row-ctl">
          <span className="lab">{LEGEND_UI.minScore}</span>
          <input type="range" min={0} max={90} step={5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} aria-label={LEGEND_UI.minScore} />
          <span className="num" style={{ fontSize: 13, fontWeight: 700, width: 28, textAlign: "right" }}>
            {minScore}
          </span>
        </div>

        {/* 주가 고도 — 기업 중심 뷰에는 고도 개념이 없다 */}
        {!focusId && (
          <>
            <div className="alt-row">
              <button type="button" className={`toggle ${altitudeOn ? "on" : ""}`} onClick={() => setAltitude(!altitudeOn)} title={LEGEND_UI.altitudeTitle}>
                {LEGEND_UI.altitude}
              </button>
              <div className="seg" role="tablist" aria-label={LEGEND_UI.altitude + " 기간"}>
                {ALTITUDE_WINDOWS.map((w) => (
                  <button key={w} type="button" role="tab" aria-selected={altitudeWindow === w} className={altitudeWindow === w ? "on" : ""} onClick={() => setAltitudeWindow(w)}>
                    {w}
                  </button>
                ))}
              </div>
            </div>
            <div className="meta legend-hint">{LEGEND_UI.altitudeHint}</div>
          </>
        )}

        <button type="button" className={`legend-adv-toggle ${advOpen ? "open" : ""}`} aria-expanded={advOpen} onClick={() => setAdvOpen((v) => !v)}>
          <Icon.Chevron />
          {LEGEND_UI.advanced}
        </button>
        {advOpen && (
          <div className="legend-adv">
            <div className="row-ctl">
              <span className="lab">{LEGEND_UI.topOnly}</span>
              <span />
              <button type="button" className={`toggle ${edgeBudget ? "on" : ""}`} onClick={toggleEdgeBudget} title={LEGEND_UI.topOnlyTitle}>
                {edgeBudget ? "ON" : "OFF"}
              </button>
            </div>
            <div className="row-ctl">
              <span className="lab">{LEGEND_UI.nameLabels}</span>
              <span />
              <button type="button" className={`toggle ${showLabels ? "on" : ""}`} onClick={toggleLabels}>
                {showLabels ? "ON" : "OFF"}
              </button>
            </div>
            <div className="row-ctl">
              <span className="lab">{LEGEND_UI.edgeLabels}</span>
              <span />
              <button type="button" className={`toggle ${showEdgeLabels ? "on" : ""}`} onClick={toggleEdgeLabels} title={LEGEND_UI.edgeLabelsTitle}>
                {showEdgeLabels ? "ON" : "OFF"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
