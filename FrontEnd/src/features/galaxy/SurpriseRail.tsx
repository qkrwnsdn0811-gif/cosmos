import { useEffect, useMemo, useState } from "react";
import { EdgeGlyph, Empty, Icon } from "@/components/ui";
import { fmtScore } from "@/lib/format";
import type { SceneModel } from "@/lib/graph";
import { SURPRISE_UI } from "@/lib/guide";
import { relationshipMeta } from "@/lib/meta";
import { rankSurprises, type Surprise } from "@/lib/surprise";
import { useGalaxy } from "@/store/galaxy";
import { beamColorFor } from "@/three/toon";
import "./surprise.css";

const NARROW_BREAKPOINT = 900;

/** 900px 미만에서는 카드를 아예 펼치지 않는다 — 접힘 여부는 store(surpriseOpen)가 아니라 뷰포트가 정한다 */
function useNarrowViewport() {
  const [narrow, setNarrow] = useState(() => (typeof window !== "undefined" ? window.innerWidth < NARROW_BREAKPOINT : false));
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(`(max-width: ${NARROW_BREAKPOINT}px)`);
    const onChange = (e: MediaQueryListEvent) => setNarrow(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

interface Props {
  /** 은하 뷰면 galaxyModel, 기업 관계망이면 systemModel — GalaxyPage 가 phase 에 맞춰 넘긴다 */
  model: SceneModel | null;
  onPickCompany: (id: string) => void;
}

/**
 * 왼쪽 "뜻밖의 관계" 레일 — "생각지도 못한 관계를 안다" 는 이 제품의 첫 약속을 화면에서 지킨다.
 * lib/surprise 의 순위를 지금 화면 필터(최소 점수·활성 유형)로 한 번 더 걸러 씬에 실제로 보이는 간선만 남긴다.
 * 행에 호버·포커스하면 씬의 그 빔이 밝아지고(기존 emphasis 규칙), 누르면 관계 패널이 근거와 함께 연다 —
 * "3클릭 내 근거" 를 1클릭으로 줄이는 지점이다.
 */
export default function SurpriseRail({ model, onPickCompany }: Props) {
  const phase = useGalaxy((s) => s.phase);
  const surpriseOpen = useGalaxy((s) => s.surpriseOpen);
  const toggleSurprise = useGalaxy((s) => s.toggleSurprise);
  const minScore = useGalaxy((s) => s.minScore);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const selectEdge = useGalaxy((s) => s.selectEdge);
  const setHoveredEdge = useGalaxy((s) => s.setHoveredEdge);
  const narrow = useNarrowViewport();

  const rows = useMemo(() => {
    if (!model) return [];
    const incidentTo = model.kind === "system" ? model.centerId : null;
    const ranked = rankSurprises(model, { limit: 6, incidentTo, perCompany: 2 });
    // 순위는 모델 전체 기준 — 지금 화면에 실제로 보이는 간선(최소 점수·활성 유형)만 한 번 더 남긴다
    return ranked.filter((s) => {
      const e = model.edgeById.get(s.edgeId);
      return e !== undefined && e.score >= minScore && activeTypes.has(e.type);
    });
  }, [model, minScore, activeTypes]);

  if (phase === "warp" || !model) return null;

  const centerId = model.kind === "system" ? model.centerId : null;
  const open = surpriseOpen && !narrow;

  if (!open) {
    return (
      <aside className="hud hud-surprise">
        <button type="button" className="hud-tag" onClick={toggleSurprise} title={SURPRISE_UI.kicker}>
          <span aria-hidden="true">{SURPRISE_UI.mark}</span> {SURPRISE_UI.kicker} · {SURPRISE_UI.expandTag(rows.length)}
        </button>
      </aside>
    );
  }

  const centerName = centerId ? model.nodeById.get(centerId)?.name : undefined;
  const title = centerName ? SURPRISE_UI.titleFor(centerName) : SURPRISE_UI.title;

  return (
    <aside className="hud hud-surprise">
      <div className="card surprise-card">
        <div className="row between surprise-head">
          <span className="kick surprise-kick">
            <span aria-hidden="true">{SURPRISE_UI.mark}</span> {SURPRISE_UI.kicker}
          </span>
          <button type="button" className="toggle" onClick={toggleSurprise} aria-label={SURPRISE_UI.collapse} title={SURPRISE_UI.collapse}>
            <Icon.Close />
          </button>
        </div>
        <div className="surprise-title">{title}</div>
        {!centerId && <p className="meta surprise-lead">{SURPRISE_UI.lead}</p>}
        {rows.length === 0 ? (
          <Empty>{centerId ? SURPRISE_UI.emptyCompany : SURPRISE_UI.empty}</Empty>
        ) : (
          <>
            <div className="stack surprise-rows">
              {rows.map((s) => (
                <SurpriseRow key={s.edgeId} model={model} surprise={s} centerId={centerId} onPickCompany={onPickCompany} selectEdge={selectEdge} setHoveredEdge={setHoveredEdge} />
              ))}
            </div>
            <div className="meta surprise-hint">{SURPRISE_UI.hoverHint}</div>
          </>
        )}
      </div>
    </aside>
  );
}

function SurpriseRow({
  model,
  surprise,
  centerId,
  onPickCompany,
  selectEdge,
  setHoveredEdge,
}: {
  model: SceneModel;
  surprise: Surprise;
  centerId: string | null;
  onPickCompany: (id: string) => void;
  selectEdge: (id: string | null) => void;
  setHoveredEdge: (id: string | null) => void;
}) {
  const edge = model.edgeById.get(surprise.edgeId);
  const a = edge ? model.nodeById.get(edge.source) : undefined;
  const b = edge ? model.nodeById.get(edge.target) : undefined;
  if (!edge || !a || !b) return null;

  const reasons = surprise.reasons.slice(0, 2);
  const enter = () => setHoveredEdge(edge.id);
  // 스토어를 구독하지 않고 getState 로 지금 값만 확인한다 — 다른 행의 호버로 이 레일 전체가 리렌더되지 않게 하기 위해서다
  const leave = () => {
    if (useGalaxy.getState().hoveredEdgeId === edge.id) setHoveredEdge(null);
  };
  const pick = (id: string) => (ev: React.MouseEvent) => {
    ev.stopPropagation();
    onPickCompany(id);
  };

  return (
    <div
      className="surprise-row"
      role="button"
      tabIndex={0}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={enter}
      onBlur={leave}
      onClick={() => selectEdge(edge.id)}
      onKeyDown={(e) => {
        // 안쪽 기업 버튼에서 올라온 키는 그 버튼의 것 — 여기서 가로채면 Enter 가 기업 이동 대신 관계 패널을 연다
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectEdge(edge.id);
        }
      }}
      title={SURPRISE_UI.tooltip(reasons.map((r) => r.label))}
    >
      <div className="row between surprise-pair-row">
        <span className="surprise-pair">
          <button type="button" className={`surprise-co ${centerId === a.id ? "is-center" : ""}`} onClick={pick(a.id)} title={`${a.name} · 관계망으로`}>
            {a.name}
          </button>
          <EdgeGlyph directed={edge.directed} color={beamColorFor(edge.impact)} />
          <button type="button" className={`surprise-co ${centerId === b.id ? "is-center" : ""}`} onClick={pick(b.id)} title={`${b.name} · 관계망으로`}>
            {b.name}
          </button>
        </span>
        <span className="num surprise-score">{fmtScore(edge.score)}</span>
      </div>
      <div className="row wrap surprise-reasons">
        <span className="meta">{relationshipMeta(edge.type).label}</span>
        {reasons.map((r) => (
          <span key={r.key} className="chip chip-sm" title={r.detail}>
            {r.label}
          </span>
        ))}
      </div>
    </div>
  );
}
