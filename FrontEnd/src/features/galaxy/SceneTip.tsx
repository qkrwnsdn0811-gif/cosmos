import { useEffect, useRef, useState, type RefObject } from "react";
import type { ImpactDirection } from "@/api/types";
import { ImpactBadge, IndustryBadge, Skeleton, TypeBadge } from "@/components/ui";
import { fmtPctFrom01, fmtRelative, fmtScore } from "@/lib/format";
import type { SceneModel } from "@/lib/graph";
import { EVIDENCE_UI, SURPRISE_UI, TIP_NODE_HINT } from "@/lib/guide";
import { IMPACT_META, relationshipMeta } from "@/lib/meta";
import { useRelationship, useTopEvidence } from "@/lib/queries";
import { ROLE_META, roleColor, roleCounts } from "@/lib/roles";
import { SURPRISE_MIN, surpriseFor } from "@/lib/surprise";
import { useGalaxy } from "@/store/galaxy";
import "./evidence.css";

export interface PointerPos {
  /** 뷰포트 기준 clientX/clientY — 툴팁이 자기 부모(.galaxy) 사각형으로 바꿔 쓴다 */
  x: number;
  y: number;
}

interface Props {
  model: SceneModel | null;
  pointer: RefObject<PointerPos>;
}

const TIP_W = 260;
const OFF_X = 14;
const OFF_Y = 16;

/** 값이 ms 동안 바뀌지 않았을 때만 따라가는 값 — 빔을 스치듯 지나갈 때 관계 상세 요청이 쏟아지지 않게 */
function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function impactLabelOf(v: ImpactDirection | null | undefined) {
  return (v && IMPACT_META[v]?.label) ?? IMPACT_META.NEUTRAL.label;
}

/**
 * 씬 툴팁(DOM) — 빔 호버는 3단으로 점진 노출한다: 0ms 관계 종류 설명(로컬) → 150ms 양쪽 기업·점수·영향·신뢰
 * (useRelationship) → 300ms 대표 근거 문장(useTopEvidence). 이유가 수치보다 먼저 와야 "왜 이어졌는지" 를
 * 숫자보다 먼저 읽게 된다. 뜻밖(surpriseFor)이면 배지·이유 요약을 맨 위에 더한다.
 * 노드 호버: 이름·산업·역할 요약. 위치는 React 상태가 아니라 포인터 ref + rAF 로 옮겨 포인터 이동마다
 * 리렌더하지 않는다. 워프 중에는 그리지 않는다.
 * 관계 상세는 RelationshipPanel 과 같은 useRelationship 키라, 툴팁이 뜬 뒤 빔을 누르면 패널이 스켈레톤 없이 렌더된다.
 */
export default function SceneTip({ model, pointer }: Props) {
  const hoveredId = useGalaxy((s) => s.hoveredId);
  const hoveredEdgeId = useGalaxy((s) => s.hoveredEdgeId);
  const phase = useGalaxy((s) => s.phase);
  const window_ = useGalaxy((s) => s.window);
  const el = useRef<HTMLDivElement>(null);

  // 노드 위에 칩·행성이 있으면 노드가 우선 (간선은 nodeAhead 로 물러난다)
  const node = model && hoveredId ? model.nodeById.get(hoveredId) : undefined;
  const edge = !node && model && hoveredEdgeId ? model.edgeById.get(hoveredEdgeId) : undefined;
  const open = phase !== "warp" && Boolean(node || edge);

  // 2행 — 150ms 머문 간선만 요청한다
  const debouncedEdgeId = useDebounced(edge?.id ?? null, 150);
  const rel = useRelationship(open && edge ? debouncedEdgeId : null, window_);
  const detail = rel.data && rel.data.relationshipId === edge?.id ? rel.data : null;

  // 3행 — 2행과 별도로 300ms 더 머물러야 대표 근거를 요청한다(스침 필터 이중화)
  const debouncedEdgeId300 = useDebounced(edge?.id ?? null, 300);
  const topEvId = open && edge && detail && detail.evidenceCount > 0 && debouncedEdgeId300 === edge.id ? debouncedEdgeId300 : null;
  const topEv = useTopEvidence(topEvId);

  // 열려 있는 동안만 rAF 로 포인터를 따라간다. 스테이지 우·하단을 넘치면 좌·상으로 뒤집는다
  useEffect(() => {
    if (!open) return;
    let raf = 0;
    const tick = () => {
      const d = el.current;
      const host = d?.parentElement;
      if (d && host && pointer.current) {
        const rect = host.getBoundingClientRect();
        const px = pointer.current.x - rect.left;
        const py = pointer.current.y - rect.top;
        const h = d.offsetHeight || 80;
        let x = px + OFF_X + TIP_W > rect.width ? px - OFF_X - TIP_W : px + OFF_X;
        let y = py + OFF_Y + h > rect.height ? py - OFF_Y - h : py + OFF_Y;
        // 뜻밖의 관계 카드의 행을 호버해 뜬 툴팁은 카드를 가리지 않게 카드 오른쪽에 나란히 세운다 (행이 미리보기, 툴팁이 근거)
        const rail = host.querySelector<HTMLElement>(".hud-surprise .surprise-card");
        if (rail) {
          const r = rail.getBoundingClientRect();
          if (pointer.current.x >= r.left && pointer.current.x <= r.right && pointer.current.y >= r.top && pointer.current.y <= r.bottom) {
            x = r.right - rect.left + 12;
            y = Math.min(py - 24, rect.height - h - 12);
          }
        }
        d.style.transform = `translate(${Math.max(0, Math.round(x))}px, ${Math.max(0, Math.round(y))}px)`;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [open, pointer]);

  if (!open || !model) return null;

  if (node) {
    const counts = roleCounts(model, node.id);
    const total = counts.reduce((s, c) => s + c.count, 0);
    return (
      <div ref={el} className="scene-tip" role="tooltip">
        <div className="tip-head">
          <b>{node.name}</b>
          {node.industry && <IndustryBadge name={node.industry} />}
        </div>
        <div className="tip-roles meta">
          <span>관계 {total}</span>
          {counts.map((rc) => (
            <span key={rc.role} className="tip-role">
              <i className="dot" style={{ background: roleColor(rc.role) }} />
              {ROLE_META[rc.role].label} {rc.count}
            </span>
          ))}
        </div>
        {/* 클릭이 워프가 아니라 미리보기라는 것은 화면만 보고는 알 수 없다 — 툴팁 한 줄로 알린다 */}
        <div className="tip-hint">{TIP_NODE_HINT}</div>
      </div>
    );
  }

  if (!edge) return null;
  const src = model.nodeById.get(edge.source);
  const tgt = model.nodeById.get(edge.target);
  const meta = relationshipMeta(edge.type);
  const surprise = surpriseFor(model, edge);
  const isSurprise = surprise.score >= SURPRISE_MIN;
  const lowConfidence = detail?.confidence != null && detail.confidence < EVIDENCE_UI.lowConfidenceBelow;
  const topItem = topEv.data?.items?.[0];

  return (
    <div ref={el} className="scene-tip" role="tooltip">
      {/* 1행(0ms, 로컬) — 이유가 숫자보다 먼저 온다 */}
      <div className="ev-desc">{meta.description}</div>

      {/* 유형·영향(·뜻밖) 배지 줄 — 1행 아래로 */}
      <div className="row between">
        <div className="row wrap" style={{ gap: 6 }}>
          {isSurprise && (
            <span className="badge ev-surprise-badge">
              {SURPRISE_UI.mark} {SURPRISE_UI.badge}
            </span>
          )}
          <TypeBadge type={edge.type} directed={edge.directed} />
        </div>
        <ImpactBadge value={edge.impact} />
      </div>
      {isSurprise && <div className="ev-surprise-line">{SURPRISE_UI.tooltip(surprise.reasons.slice(0, 2).map((r) => r.label))}</div>}

      {/* 2행(150ms, useRelationship) — 도착 전엔 고정 높이 스켈레톤 */}
      <div className="ev-detail meta num">
        {detail ? (
          <>
            {meta.label} · {src?.name ?? "?"} {edge.directed ? "→" : "↔"} {tgt?.name ?? "?"} · {fmtScore(detail.score)}점 · {impactLabelOf(detail.impactDirection)} 영향 ·{" "}
            <span className={lowConfidence ? "ev-warn" : undefined}>{EVIDENCE_UI.confidence(fmtPctFrom01(detail.confidence))}</span>
          </>
        ) : (
          <Skeleton h={12} w={200} />
        )}
      </div>

      {/* 3행(300ms) — 대표 근거 문장. 높이가 튀면 커서 아래 요소가 흔들려 호버가 끊긴다 */}
      <div className="ev-quote-row">
        {!detail ? (
          <Skeleton h={34} />
        ) : detail.evidenceCount === 0 ? (
          <div className="ev-no-evidence">{EVIDENCE_UI.noEvidence}</div>
        ) : debouncedEdgeId300 !== edge.id ? (
          <Skeleton h={34} />
        ) : topItem ? (
          // 캐시에 있는 문장은 상한과 무관하게 보여 준다 — 상한은 "새 요청" 을 막는 것이지 이미 받은 답을 가리는 것이 아니다
          <>
            <div className="ev-quote">
              <q>{topItem.evidenceSentence}</q> — {[topItem.publisher, fmtRelative(topItem.publishedAt)].filter(Boolean).join(" · ")}
            </div>
            <div className="ev-more">{EVIDENCE_UI.hoverMore(detail.evidenceCount)}</div>
          </>
        ) : topEv.capped ? (
          <div className="ev-no-evidence">{EVIDENCE_UI.hoverCapped(detail.evidenceCount)}</div>
        ) : (
          <Skeleton h={34} />
        )}
      </div>
    </div>
  );
}
