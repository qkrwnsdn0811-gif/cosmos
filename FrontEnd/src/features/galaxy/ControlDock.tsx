import { useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { AltitudeWindow } from "@/api/types";
import { Icon } from "@/components/ui";
import { DOCK_UI } from "@/lib/guide";
import { METRIC_WINDOWS, METRIC_WINDOW_LABEL, RELATIONSHIP_ORDER, relationshipMeta } from "@/lib/meta";
import { spreadStage } from "@/lib/spread";
import { useGalaxy } from "@/store/galaxy";
import { pct, useWeight } from "@/store/weight";
import "./dock.css";

/**
 * 좌측 세로 조절판 — 예전엔 하단 중앙 가로 도크였지만, Special Link(SurpriseRail) 카드와 자리를 맞바꿔
 * 이제 왼쪽 열(구 hud-surprise 자리)에 세로로 길게 선다.
 * 기본은 펼친 상태이고, 접으면 왼쪽 알약 버튼 하나만 남는다(store/galaxy 의 dockOpen). 펼치면 머리줄(라벨 +
 * 초기화·접기) 아래로 펼치기 · 관계 종류 · 뉴스·공시 비율 · 최소 점수 · 주가 고도 · 관계선 기준 기간이 세로로 쌓인다.
 * 예전에는 이 중 뒤쪽(고급) 줄들을 '>' 로 접어 별도 장으로 감췄지만, 뉴스·공시 비율·최소 점수는 늘 눈에 보여야
 * 한다는 결정에 따라 그 토글은 없앴다 — 조절판을 열면 처음부터 전부 보인다.
 * 왼쪽 열은 다른 HUD(뉴스 복귀 팝업·1홉 요약 스트립)도 같이 쓰므로, 그 줄들이 있을 때 아래로 비켜서는 값은
 * galaxy.css 의 "HUD 사이의 자리 다툼" 절에서 관리한다.
 *
 * 관계선 기준 기간(edgeWindow, 주가 고도 바로 아래): 은하 뷰와 기업 중심 뷰 모두에서 보인다(주가 고도는 은하 뷰만). 고르는 값은
 * store/galaxy 의 전역 상태라 화면을 옮겨도(은하 ↔ 기업 중심 뷰 ↔ 관계 경로 화면) 그대로 유지되어 그 화면들이
 * 요청하는 그래프에도 같이 적용된다(lib/queries 의 useLatestGraph·useCompanyGraph 가 window 쿼리로 넘긴다) —
 * "이미 고른 관계 하나의 지표를 보는 기간"(CompanyPanel/RelationshipPanel 의 window)과는 달리, 이 값은
 * 관계선 자체(간선의 존재·점수)를 다시 계산하는 기준이다.
 */

const ALTITUDE_WINDOWS: AltitudeWindow[] = ["7D", "30D", "90D"];
/** 펼치기 버튼 세 값 — 은하 / 평면 / 정렬. SpreadDriver·Director 는 spread 값이 어떻게 바뀌든(연속 드래그든 이 버튼의 순간 점프든)
 * 매 프레임 목표값으로 부드럽게 보간하므로, 버튼으로 값만 바꿔 줘도 기존과 같은 자연스러운 전환이 그대로 재생된다 */
const SPREAD_STOPS: Record<"galaxy" | "flat" | "sorted", number> = { galaxy: 0, flat: 0.5, sorted: 1 };

export default function ControlDock() {
  const dockOpen = useGalaxy((s) => s.dockOpen);
  const setDockOpen = useGalaxy((s) => s.setDockOpen);
  const spread = useGalaxy((s) => s.spread);
  const setSpread = useGalaxy((s) => s.setSpread);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const toggleType = useGalaxy((s) => s.toggleType);
  const minScore = useGalaxy((s) => s.minScore);
  const setMinScore = useGalaxy((s) => s.setMinScore);
  const altitudeOn = useGalaxy((s) => s.altitudeOn);
  const setAltitude = useGalaxy((s) => s.setAltitude);
  const altitudeWindow = useGalaxy((s) => s.altitudeWindow);
  const setAltitudeWindow = useGalaxy((s) => s.setAltitudeWindow);
  const edgeWindow = useGalaxy((s) => s.edgeWindow);
  const setEdgeWindow = useGalaxy((s) => s.setEdgeWindow);
  const focusId = useGalaxy((s) => s.focusId);
  const resetControls = useGalaxy((s) => s.resetControls);

  const newsWeight = useWeight((s) => s.newsWeight);
  const setNewsWeight = useWeight((s) => s.setNewsWeight);
  /**
   * 뉴스·공시 비율 슬라이더의 드래그 중 표시값 — 드래그하는 동안은 이 값만 바뀌고(라벨만 갱신),
   * 실제 store(useWeight)·씬 재계산은 손을 떼거나(pointerUp) 키를 놓을 때(keyUp)만 한 번 커밋한다.
   * 매 onChange마다 커밋하면 은하 씬의 간선 지오메트리가 매 틱 다시 만들어져 드래그 중 렉이 생긴다.
   */
  const [draftNewsWeightPct, setDraftNewsWeightPct] = useState<number | null>(null);

  if (!dockOpen) {
    return (
      <div className="hud hud-dock">
        <button type="button" className="dock-open" onClick={() => setDockOpen(true)} title={DOCK_UI.open} aria-expanded={dockOpen}>
          <Icon.Chevron />
        </button>
      </div>
    );
  }

  const stage = spreadStage(spread);
  const inGalaxyView = focusId === null;
  const newsWeightPct = pct(newsWeight);

  /**
   * 마우스로 슬라이더를 놓으면 포커스를 떼어 준다 — 포커스가 남아 있으면 방향키가 씬이 아니라 이 슬라이더로 가서
   * "슬라이더를 만진 뒤 우주를 한 번 클릭해야" 카메라가 움직였다. 키보드(Tab)로 온 경우에는 그대로 둔다.
   */
  function releaseFocus(e: ReactPointerEvent<HTMLInputElement>) {
    e.currentTarget.blur();
  }
  function handleReset() {
    resetControls();
    useWeight.getState().reset();
    setDraftNewsWeightPct(null);
  }
  function releaseNewsWeight(e: ReactPointerEvent<HTMLInputElement> | ReactKeyboardEvent<HTMLInputElement>) {
    setNewsWeight(Number(e.currentTarget.value) / 100);
    setDraftNewsWeightPct(null);
  }

  /* ---- 조절 수단 조각 — 넓은 화면과 모바일 두 장이 같은 조각을 다른 자리에 놓는다 ---- */

  const spreadCol = (
    <div className="dock-col dock-spread">
      <div className="dock-row-head">
        <span className="lab" title={DOCK_UI.spreadTitle}>
          {DOCK_UI.spread}
        </span>
      </div>
      {/* 예전엔 0~100% 슬라이더로 중간값도 고를 수 있었지만, 세 단계 사이 미세 조정이 굳이 필요하지 않다는 판단에
          은하/평면/정렬 세 버튼으로 바꿨다 — setSpread 로 값만 바뀌면 SpreadDriver 가 그 값까지 알아서 부드럽게
          보간하므로, 느린 드래그로 만들던 자연스러운 전환은 버튼을 눌러도 그대로다 */}
      <div className="seg dock-stages-btn">
        {(Object.keys(SPREAD_STOPS) as Array<keyof typeof SPREAD_STOPS>).map((k) => (
          <button key={k} type="button" className={stage === k ? "on" : undefined} onClick={() => setSpread(SPREAD_STOPS[k])} title={DOCK_UI.spreadTitle}>
            {DOCK_UI.stages[k]}
          </button>
        ))}
      </div>
    </div>
  );

  const typeChips = RELATIONSHIP_ORDER.map((t) => {
    const m = relationshipMeta(t);
    const on = activeTypes.has(t);
    return (
      <button key={t} type="button" className={`chip chip-sm ${on ? "on" : ""}`} onClick={() => toggleType(t)} title={m.description}>
        {m.label}
      </button>
    );
  });

  const displayNewsWeightPct = draftNewsWeightPct ?? newsWeightPct;
  const mixRow = (
    <div className="dock-row dock-mix">
      {/* 라벨이 곧 값이다 — "뉴스 50 · 공시 50" 한 줄로 비율을 읽는다. 드래그 중에는 draft 값을 보여 주고, 실제 반영은 놓을 때 한 번뿐이다 */}
      <span className="lab num dock-mix-val" title={DOCK_UI.mixTitle}>
        뉴스 <b>{displayNewsWeightPct}</b> · 공시 <b>{100 - displayNewsWeightPct}</b>
      </span>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={displayNewsWeightPct}
        onChange={(e) => setDraftNewsWeightPct(Number(e.target.value))}
        onPointerUp={(e) => {
          releaseNewsWeight(e);
          releaseFocus(e);
        }}
        onKeyUp={releaseNewsWeight}
        title={DOCK_UI.mixTitle}
        aria-label={DOCK_UI.mix}
      />
    </div>
  );

  const minScoreRow = (
    <div className="dock-row dock-minscore">
      <span className="lab">{DOCK_UI.minScore}</span>
      <input type="range" min={0} max={90} step={5} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} onPointerUp={releaseFocus} aria-label={DOCK_UI.minScore} />
      <span className="meta num">{minScore}</span>
    </div>
  );

  const altitudeRow = (
    <div className="dock-row dock-altitude">
      <button
        type="button"
        className={`toggle ${altitudeOn ? "on" : ""}`}
        onClick={() => setAltitude(!altitudeOn)}
        disabled={spread > 0}
        title={spread > 0 ? DOCK_UI.altitudeSuspended : DOCK_UI.altitudeTitle}
      >
        {DOCK_UI.altitude}
      </button>
      <div className="seg">
        {ALTITUDE_WINDOWS.map((w) => (
          <button key={w} type="button" className={altitudeWindow === w ? "on" : ""} onClick={() => setAltitudeWindow(w)} disabled={spread > 0} title={spread > 0 ? DOCK_UI.altitudeSuspended : undefined}>
            {w}
          </button>
        ))}
      </div>
    </div>
  );

  /* 관계선(간선) 자체를 다시 계산하는 기준 기간 — 주가 고도 바로 옆. 여기서 고르면 메인 은하뿐 아니라 기업 중심 뷰·
     관계 경로 화면·관계 검색 추천까지 같은 기간 기준으로 관계선을 다시 받는다(store/galaxy 의 edgeWindow, 전역) */
  const edgeWindowRow = (
    <div className="dock-row dock-edge-window">
      <span className="lab" title={DOCK_UI.edgeWindowTitle}>
        {DOCK_UI.edgeWindow}
      </span>
      <div className="seg">
        {METRIC_WINDOWS.map((w) => (
          <button key={w} type="button" className={edgeWindow === w ? "on" : ""} onClick={() => setEdgeWindow(w)} title={DOCK_UI.edgeWindowTitle}>
            {METRIC_WINDOW_LABEL[w]}
          </button>
        ))}
      </div>
    </div>
  );

  /* 머리줄 — Special Link 카드(surprise-head)와 같은 구조: 왼쪽 kick 라벨, 오른쪽에 초기화·접기를 아이콘 버튼으로 묶는다.
     예전(하단 가로 도크)에는 초기화가 카드 맨 끝 아이콘, 접기는 카드 위로 튀어나온 손잡이였는데, 좌측 세로 카드로
     바뀌며 그 자리들이 어색해져 Special Link 와 같은 머리줄 패턴으로 통일했다 */
  const head = (
    <div className="row between dock-head">
      <span className="kick dock-kick">{DOCK_UI.kick}</span>
      <span className="row dock-head-actions">
        <button type="button" className="dock-icon dock-reset" onClick={handleReset} title={DOCK_UI.resetTitle} aria-label={DOCK_UI.reset}>
          <Icon.Refresh />
        </button>
        <button type="button" className="toggle" onClick={() => setDockOpen(false)} title={DOCK_UI.close} aria-label={DOCK_UI.close}>
          <Icon.Close />
        </button>
      </span>
    </div>
  );

  return (
    <div className="hud hud-dock">
      <div className="card dock-card">
        {head}

        {inGalaxyView && (
          <>
            {spreadCol}
            <span className="dock-div" aria-hidden="true" />
          </>
        )}

        <div className="dock-col dock-types" title={DOCK_UI.types}>
          {typeChips}
        </div>

        <span className="dock-div" aria-hidden="true" />
        <div className="dock-col dock-advanced">
          {mixRow}
          {minScoreRow}
          {inGalaxyView && altitudeRow}
          {edgeWindowRow}
        </div>
      </div>
    </div>
  );
}
