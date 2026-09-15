import type { MetricWindow, RelationshipEvidence } from "@/api/types";
import { Empty, Icon, ImpactBadge, ScoreBar, Skeleton, TypeBadge } from "@/components/ui";
import { fmtDateTime, fmtPctFrom01, fmtRelative, fmtScore, pct01 } from "@/lib/format";
import type { SceneModel } from "@/lib/graph";
import { EVIDENCE_UI, SURPRISE_UI } from "@/lib/guide";
import { relationshipMeta, scoreBand } from "@/lib/meta";
import { useRelationship, useRelationshipEvidence } from "@/lib/queries";
import { scoreBasisLabel } from "@/lib/score";
import { SURPRISE_MIN, surpriseFor } from "@/lib/surprise";
import { useGalaxy } from "@/store/galaxy";
import { useUi } from "@/store/ui";
import { useNewsWeight } from "@/store/weight";
import "./evidence.css";

const WINDOWS: MetricWindow[] = ["7D", "30D", "90D"];

interface Props {
  relationshipId: string;
  onClose: () => void;
  onPickCompany: (id: string) => void;
  /** 뜻밖 판정(surpriseFor)에 필요 — 없으면 "왜 뜻밖인가" 블록은 그리지 않는다 */
  model?: SceneModel | null;
}

/** 이 근거의 나이(발행 후 경과일)에 맞는 불투명도 — 숨기지 않고 물러나게만(EVIDENCE_UI.ageOpacity) */
function ageOpacityOf(publishedAt: string | null) {
  if (!publishedAt) return EVIDENCE_UI.ageOpacity[EVIDENCE_UI.ageOpacity.length - 1].opacity;
  const days = (Date.now() - new Date(publishedAt).getTime()) / 86_400_000;
  const band = EVIDENCE_UI.ageOpacity.find((b) => days <= b.maxDays);
  return band?.opacity ?? 1;
}

/** 근거 문장 복사 — Clipboard API 실패 시(비보안 컨텍스트·권한 거부) textarea 폴백 */
async function copyEvidenceSentence(e: RelationshipEvidence) {
  const text = `"${e.evidenceSentence}" — ${e.publisher ?? ""}, ${(e.publishedAt ?? "").slice(0, 10)}, ${e.originalUrl}`;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
    await navigator.clipboard.writeText(text);
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    } catch {
      return; // 복사 실패 — 조용히 포기, 성공 토스트를 내지 않는다
    }
  }
  useUi.getState().toast(EVIDENCE_UI.copied, "success");
}

/** 관계선 선택 시: 자동 추출 고지·(뜻밖이면) 이유·양쪽 기업·유형·점수·영향 방향 + 근거 뉴스 타임라인 */
export default function RelationshipPanel({ relationshipId, onClose, onPickCompany, model }: Props) {
  const window = useGalaxy((s) => s.window);
  const setWindow = useGalaxy((s) => s.setWindow);
  const rel = useRelationship(relationshipId, window);
  const evidence = useRelationshipEvidence(relationshipId);
  const newsWeight = useNewsWeight();
  const r = rel.data;
  // 점수를 무엇으로 만들었는지 — 비율을 움직였거나 한쪽 근거만 있을 때만 한 줄 붙는다
  const basis = r ? scoreBasisLabel(r, newsWeight) : null;
  const meta = r ? relationshipMeta(r.relationshipType) : null;
  const items = evidence.data?.pages.flatMap((p) => p.items) ?? [];
  const sortedItems = [...items].sort((a, b) => new Date(b.publishedAt ?? 0).getTime() - new Date(a.publishedAt ?? 0).getTime());
  const representativeId = sortedItems.reduce<{ id: string; score: number } | null>((best, e) => {
    const s = e.contributionScore ?? -Infinity;
    return !best || s > best.score ? { id: e.newsId, score: s } : best;
  }, null)?.id;

  const edge = model && r ? model.edgeById.get(relationshipId) : undefined;
  const surprise = model && edge ? surpriseFor(model, edge) : null;
  const isSurprise = (surprise?.score ?? 0) >= SURPRISE_MIN;
  const lowConfidence = r?.confidence != null && r.confidence < EVIDENCE_UI.lowConfidenceBelow;

  return (
    <aside className="card panel" aria-label="기업 관계 상세">
      <div className="panel-head">
        <span className="kick">기업 관계</span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="닫기">
          <Icon.Close />
        </button>
      </div>
      <div className="panel-body scroll">
        {!r || !meta ? (
          <>
            <Skeleton h={72} style={{ borderRadius: 12 }} />
            <Skeleton h={40} style={{ marginTop: 16 }} />
          </>
        ) : (
          <>
            <div className="row between">
              <div className="row wrap" style={{ gap: 8 }}>
                <TypeBadge type={r.relationshipType} directed={r.directionality === "DIRECTED"} />
                <ImpactBadge value={r.impactDirection} />
              </div>
              <div className="row" style={{ gap: 6 }}>
                {lowConfidence && <span className="ev-low-confidence">{EVIDENCE_UI.lowConfidence}</span>}
                <span
                  className="badge"
                  style={{ background: lowConfidence ? "rgba(245,166,35,.14)" : "rgba(255,255,255,.06)", color: lowConfidence ? "var(--warn)" : "var(--text-3)" }}
                  title={EVIDENCE_UI.autoExtractedHelp}
                >
                  {EVIDENCE_UI.autoExtracted} ⓘ
                </span>
              </div>
            </div>
            <div className="rel-pair mt-12">
              <button type="button" className="end" onClick={() => onPickCompany(r.sourceCompany.companyId)} title="이 기업 중심으로 보기">
                <span className="meta">{r.directionality === "DIRECTED" ? "source" : "기업 A"}</span>
                <b>{r.sourceCompany.name}</b>
              </button>
              <span className="arrow" aria-hidden="true">
                {r.directionality === "DIRECTED" ? "→" : "↔"}
              </span>
              <button type="button" className="end" onClick={() => onPickCompany(r.targetCompany.companyId)} title="이 기업 중심으로 보기">
                <span className="meta">{r.directionality === "DIRECTED" ? "target" : "기업 B"}</span>
                <b>{r.targetCompany.name}</b>
              </button>
            </div>
            <div className="meta mt-8" style={{ lineHeight: 1.6 }}>
              {meta.description}
            </div>

            <div className="row between mt-20">
              <span className="kick">관계 점수</span>
              <div className="seg" style={{ padding: 3 }}>
                {WINDOWS.map((w) => (
                  <button key={w} type="button" className={window === w ? "on" : ""} style={{ padding: "4px 9px", fontSize: 12 }} onClick={() => setWindow(w)}>
                    {w}
                  </button>
                ))}
              </div>
            </div>
            <div className="row mt-12" style={{ alignItems: "flex-end", gap: 14 }}>
              <div className="score-big num" style={{ color: meta.color }}>
                {fmtScore(r.score)}
              </div>
              <div className="grow">
                <div className="lab">{scoreBand(r.score)} · 100점 기준</div>
                <div className="mt-8" style={{ display: "flex" }}>
                  <ScoreBar score={r.score} color={meta.color} />
                </div>
              </div>
            </div>

            {isSurprise && surprise && (
              <div className="ev-why mt-16">
                <div className="kick ev-why-title">{SURPRISE_UI.why}</div>
                <ul className="ev-why-list">
                  {surprise.reasons.map((rs) => (
                    <li key={rs.key}>
                      <b>{rs.label}</b>
                      <span>{rs.detail}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="stat-grid mt-16">
              <div className="stat">
                <div className="lab">신뢰도</div>
                <div className="v num">{fmtPctFrom01(r.confidence)}</div>
              </div>
              <div className="stat">
                <div className="lab">근거 문서</div>
                <div className="v num">
                  {r.evidenceCount} <span className="lab">건</span>
                </div>
              </div>
            </div>
            <div className="meta mt-12 num">
              기준 시각 {fmtDateTime(r.asOfAt)}
              {basis && ` · ${basis}`}
            </div>

            <div className="row between mt-20">
              <span className="kick">{EVIDENCE_UI.timelineKicker}</span>
              <span className="meta">{EVIDENCE_UI.timelineOrder}</span>
            </div>
            {sortedItems.length > 0 && <div className="meta ev-timeline-summary">{EVIDENCE_UI.timelineSummary(sortedItems.length, fmtRelative(sortedItems[0].publishedAt))}</div>}

            <div className="ev-timeline mt-12">
              {evidence.isLoading && [0, 1].map((i) => <Skeleton key={i} h={96} style={{ borderRadius: 14 }} />)}
              {!evidence.isLoading && !sortedItems.length && (
                <div>
                  <Empty>{EVIDENCE_UI.noEvidence}</Empty>
                  <div className="meta mt-8">{EVIDENCE_UI.disclosure}</div>
                </div>
              )}
              {sortedItems.map((e) => (
                <div key={e.newsId} className="ev-item" style={{ opacity: ageOpacityOf(e.publishedAt) }}>
                  <div className="ev-item-rail" aria-hidden="true">
                    <i className="ev-item-dot" />
                  </div>
                  <div className="ev-item-body">
                    <div className="row between">
                      <span className="meta num">{[e.publisher, fmtRelative(e.publishedAt)].filter(Boolean).join(" · ")}</span>
                      <span className="meta num">
                        {EVIDENCE_UI.contribution} {(e.contributionScore ?? 0).toFixed(2)}
                      </span>
                    </div>
                    <span className="bar ev-item-bar">
                      <i style={{ width: `${pct01(e.contributionScore)}%`, background: meta.color }} />
                    </span>
                    {e.newsId === representativeId && <span className="badge ev-rep-badge">{EVIDENCE_UI.representative}</span>}
                    <div className="ev-item-quote">
                      <q>{e.evidenceSentence}</q>
                    </div>
                    <div className="ev-item-actions">
                      <button type="button" className="link-btn" onClick={() => void copyEvidenceSentence(e)}>
                        {EVIDENCE_UI.copy}
                      </button>
                      <a href={e.originalUrl} target="_blank" rel="noreferrer" className="link-btn row" style={{ gap: 4 }}>
                        {EVIDENCE_UI.original} <Icon.External />
                      </a>
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {evidence.hasNextPage && (
              <button type="button" className="more-btn" onClick={() => evidence.fetchNextPage()} disabled={evidence.isFetchingNextPage}>
                {EVIDENCE_UI.more}
              </button>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
