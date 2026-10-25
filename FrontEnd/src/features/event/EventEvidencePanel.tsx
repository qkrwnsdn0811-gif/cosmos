import type { AbnormalReturn, EventMap, EventNode, PathStep } from "@/api/types";
import { AFTER_NEWS, afterNews } from "@/features/invest/investCopy";
import { fmtDate, fmtDateTime, fmtPct } from "@/lib/format";
import { relationshipMeta } from "@/lib/meta";
import { IMPACT_DISCLAIMER, impactLine } from "./nodeStyle";

const NO_EVIDENCE_TEXT: Record<string, string> = {
  COSMOS: "COSMOS 관계 데이터(2026-09 기준)",
  COMENTION: "같은 문장 동시언급 통계 (관계 근거 아님)",
  NEWS: "근거 조각 없음",
};

function relLabel(rel: string | null) {
  if (!rel) return "관계";
  return rel === "CO_MENTIONED" ? "동시언급" : relationshipMeta(rel).label;
}

function OriginalLink({ url, label = "원문 보기" }: { url: string; label?: string }) {
  if (!url) return null;
  return (
    <a className="event-link" href={url} target="_blank" rel="noopener noreferrer">
      {label} ↗
    </a>
  );
}

function ReturnsTable({ returns }: { returns: AbnormalReturn[] }) {
  if (!returns.length) return <p className="meta">초과수익률 데이터 없음</p>;
  return (
    <table className="event-returns">
      <thead>
        <tr>
          <th>기간</th>
          <th>주가</th>
          <th>지수</th>
          <th>초과수익률</th>
        </tr>
      </thead>
      <tbody>
        {returns.map((r) => (
          <tr key={r.windowDays}>
            <td>{r.windowDays}일</td>
            <td className="num">{fmtPct(r.stockReturn === null ? null : r.stockReturn * 100, 2)}</td>
            <td className="num">
              {fmtPct(r.benchmarkReturn === null ? null : r.benchmarkReturn * 100, 2)} <span className="meta">{r.benchmark}</span>
            </td>
            <td className={`num ${r.abnormalReturn !== null && r.abnormalReturn > 0 ? "pos" : r.abnormalReturn !== null && r.abnormalReturn < 0 ? "neg" : ""}`}>
              {fmtPct(r.abnormalReturn === null ? null : r.abnormalReturn * 100, 2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StepView({ step, dateline }: { step: PathStep; dateline: string }) {
  const source = step.sourceName ?? "마스터에 없는 기업";
  const target = step.targetName ?? "마스터에 없는 기업";
  return (
    <li className="event-step">
      <div className="event-step-head">
        <b>{source}</b> → <b>{target}</b> <span className="meta">· {relLabel(step.relationshipType)}</span>
      </div>
      {step.evidence.length ? (
        <ul className="event-snippets">
          {step.evidence.map((s) => (
            <li key={`${s.bigkindsNewsId}-${s.snippet.slice(0, 12)}`}>
              <p className="event-snippet">“{s.snippet}”</p>
              <div className="meta">
                {[s.publisher, fmtDate(s.publishedAt), afterNews(s.publishedAt, dateline) ? AFTER_NEWS : null].filter(Boolean).join(" · ")}{" "}
                <OriginalLink url={s.originalUrl} />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="meta">{NO_EVIDENCE_TEXT[step.edgeSource ?? ""] ?? "근거 조각 없음"}</p>
      )}
    </li>
  );
}

/** 영향 모델 결과 한 줄과 고지 문구. 모델이 돌지 않은 노드(해외 상장·판단 보류)에는 아무것도 그리지 않는다 */
function ImpactLine({ node }: { node: EventNode }) {
  const line = impactLine(node);
  if (!line) return null;
  return (
    <>
      <p className="event-impact">
        <b>{line}</b>
      </p>
      <p className="meta">{IMPACT_DISCLAIMER}</p>
    </>
  );
}

function NodeView({ node, map }: { node: EventNode; map: EventMap }) {
  if (node.basis === "REPORTED") {
    return (
      <>
        <div className="event-panel-name">{node.name}</div>
        <span className="badge event-badge reported">기사에서 직접 언급(근거)</span>
        <ImpactLine node={node} />
        <h4 className="event-panel-sub">관측된 초과수익률</h4>
        <ReturnsTable returns={node.abnormalReturns} />
        <div className="mt-12">
          <OriginalLink url={map.event.originalUrl} label="기사 원문 보기" />
        </div>
      </>
    );
  }
  const hold = node.status === "HOLD";
  return (
    <>
      <div className="event-panel-name">{node.name}</div>
      <span className={`badge event-badge ${hold ? "hold" : "inferred"}`}>
        {hold ? `판단 보류 · ${node.holdReason ?? "사유 없음"}` : "추정(공급망 전이)"}
      </span>
      <ImpactLine node={node} />
      <p className="meta mt-8">
        전이 점수 <b className="num">{node.score === null ? "-" : node.score.toFixed(2)}</b> · {node.hop}단계
      </p>
      <h4 className="event-panel-sub">전이 경로와 근거</h4>
      <ol className="event-steps">
        {node.path.map((step, i) => (
          <StepView key={i} step={step} dateline={map.event.dateline} />
        ))}
      </ol>
      <h4 className="event-panel-sub">관측된 초과수익률</h4>
      <ReturnsTable returns={node.abnormalReturns} />
    </>
  );
}

/**
 * 근거 패널 — 지도에서 고른 기업이 왜 나왔는지 보여준다.
 * 언급 기업은 기사 자체가 근거이고, 추정 기업은 경로의 각 관계마다 근거 조각(또는 출처 설명)을 붙인다.
 */
export default function EventEvidencePanel({ map, companyId }: { map: EventMap; companyId: string | null }) {
  const node = companyId ? map.nodes.find((n) => n.companyId === companyId) : undefined;
  const reportedCount = map.nodes.filter((n) => n.basis === "REPORTED").length;
  return (
    <aside className="card event-panel" aria-label="근거 패널">
      {node ? (
        <NodeView node={node} map={map} />
      ) : (
        <>
          <div className="kick">기사</div>
          <div className="event-panel-name">{map.event.title}</div>
          <p className="meta">{[map.event.publisher, fmtDateTime(map.event.dateline)].filter(Boolean).join(" · ")}</p>
          <p className="meta mt-8">
            언급 <b className="num">{reportedCount}</b> · 추정 <b className="num">{map.event.inferredCount}</b> · 보류{" "}
            <b className="num">{map.event.holdCount}</b>
          </p>
          <div className="mt-12">
            <OriginalLink url={map.event.originalUrl} />
          </div>
          <p className="meta mt-16">지도에서 기업을 누르면 근거와 전이 경로가 여기에 나옵니다.</p>
        </>
      )}
    </aside>
  );
}
