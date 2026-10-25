import { Link } from "react-router-dom";
import type { EventNode, EventSummary, Horizon } from "@/api/types";
import { CompanyAvatar } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { companyPath, galaxyCompanyPath } from "@/lib/routes";
import {
  AFTER_NEWS,
  afterNews,
  fmtPct,
  metricsText,
  moveBadge,
  moveFor,
  moveLine,
  pastLine,
  pathText,
  signalFor,
  signalText,
  stockCardId,
  toneOf,
  whyText,
} from "./investCopy";
import PriceChart from "./PriceChart";

const NO_EVIDENCE: Record<string, string> = {
  COSMOS: "COSMOS 기존 관계 데이터 (2026-09 기준)",
  COMENTION: "같은 문장에 함께 나온 통계 — 관계의 근거는 아님",
  NEWS: "근거 문장 없음",
};

/**
 * 영향 종목 카드(F5) — 접힌 상태에서도 딱지(F4)·예상 변동폭·과거 사례 한 줄(F3)·지표(F8)·영향 경로를 보여 주고,
 * 펼치면 이번 뉴스 뒤 실제 초과수익률, 뉴스 전후 주가 차트(F7), 근거 문장과 원문 링크(F6)가 나온다.
 */
export default function StockCard({
  node,
  event,
  horizon,
  moveGraded,
  industryOf,
  open,
  onToggle,
}: {
  node: EventNode;
  event: EventSummary;
  horizon: Horizon;
  /** 이 기간의 변동폭 등급이 시험 기간 검증을 통과했는가 (gradesHold) */
  moveGraded: boolean;
  /** 경로 위 다른 기업의 업종 — "왜 이 종목?" 문장에 쓴다 */
  industryOf: (name: string) => string | null;
  open: boolean;
  onToggle: () => void;
}) {
  const signal = signalFor(node, horizon);
  const tone = toneOf(signal?.label);
  const past = pastLine(signal, event.newsTypeLabel, node.positionLabel);
  const move = moveFor(node, horizon);
  const why = whyText(node, event.newsType, industryOf, { title: event.title, reported: event.reportedCompanies.map((c) => c.name) });
  const badge = moveBadge(move, moveGraded);
  const path = pathText(node);
  const metrics = metricsText(node.fundamental, horizon);
  const weak = node.status === "HOLD";
  const realized = node.abnormalReturns.filter((r) => [1, 3, 5, 20].includes(r.windowDays));
  const role = node.basis === "REPORTED" ? "기사에 나온 기업" : node.positionLabel ?? `${node.hop}단계 추정`;

  return (
    <article id={node.companyId ? stockCardId(node.companyId) : undefined} className={`card invest-stock ${open ? "open" : ""} ${weak ? "weak" : ""}`}>
      <button type="button" className="invest-stock-head" onClick={onToggle} aria-expanded={open}>
        <CompanyAvatar name={node.name} companyId={node.companyId} stockCode={node.stockCode} market={node.market} size={38} radius={19} />
        <span className="grow invest-stock-id">
          <span className="row wrap" style={{ gap: 6 }}>
            <b className="invest-stock-name">{node.name}</b>
            <span className={`invest-role ${node.basis === "REPORTED" ? "reported" : ""}`}>{role}</span>
            {node.stockCode && <span className="meta num">{node.stockCode}</span>}
          </span>
          {metrics && <span className="meta invest-metrics">{metrics}</span>}
        </span>
        <span className="invest-badges">
          <span className={`invest-badge ${tone}`}>{signalText(signal)}</span>
          {badge && <span className={`invest-move ${moveGraded && move?.grade === "LARGE" ? "large" : ""}`}>{badge}</span>}
        </span>
      </button>

      <div className="invest-stock-body">
        {why && (
          <p className="invest-why">
            <span className="lab">왜 이 종목?</span> {why}
          </p>
        )}
        {past ? <p className="invest-past">{past}</p> : null}
        {move && <p className="invest-move-line">{moveLine(move, moveGraded)}</p>}
        {signal?.caseScope === "POSITION" && signal.caseCount > 0 && (
          <p className="meta">이 종목만의 사례가 10건이 안 되어 같은 위치 기업 전체 사례를 썼습니다.</p>
        )}
        {path && <p className="meta invest-path">영향 경로: {path}</p>}
        {weak && node.holdReason && <p className="meta">공급망 연결이 약해 판단을 보류했습니다 ({node.holdReason}).</p>}
      </div>

      {open && (
        <div className="invest-stock-more">
          {realized.length > 0 && (
            <div className="invest-realized" aria-label="이번 뉴스 뒤 실제 초과수익률">
              <span className="lab">이번 뉴스 뒤 실제 초과수익률</span>
              {realized.map((r) => (
                <span key={r.windowDays} className={`num ${r.abnormalReturn != null && r.abnormalReturn > 0 ? "pos" : r.abnormalReturn != null && r.abnormalReturn < 0 ? "neg" : ""}`}>
                  {r.windowDays}거래일 {fmtPct(r.abnormalReturn)}
                </span>
              ))}
            </div>
          )}
          {node.companyId &&
            (event.tradingDate ? (
              <PriceChart eventId={event.eventId} companyId={node.companyId} name={node.name} />
            ) : (
              // 주말·장 마감 뒤 뉴스는 다음 거래일 주가가 들어와야 뉴스 거래일이 정해진다 — 오류가 아니라 아직 없는 것
              <p className="invest-muted">아직 이 뉴스 뒤 거래일이 오지 않아 주가 차트와 실제 반응이 없습니다.</p>
            ))}
          <Evidence node={node} event={event} />
          <div className="row wrap invest-links">
            {node.companyId && (
              <Link className="btn btn-g btn-xs" to={companyPath(node.companyId)}>
                종목 상세 · 뉴스 이력
              </Link>
            )}
            {node.companyId && (
              <Link className="btn btn-g btn-xs" to={galaxyCompanyPath(node.companyId)}>
                관계 은하에서 보기
              </Link>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

/** 이미 따옴표로 시작하는 기사 문장(인용으로 시작하는 요약 등)에는 따옴표를 겹쳐 붙이지 않는다 */
function quoted(text: string) {
  const t = text.trim();
  return /^["“‘']/.test(t) ? t : `“${t}”`;
}

/** 근거(F6) — 기사에 나온 기업은 이 기사 자체가, 추정 기업은 경로 단계마다 관계를 말한 기사 문장이 근거다 */
function Evidence({ node, event }: { node: EventNode; event: EventSummary }) {
  if (node.basis === "REPORTED") {
    return (
      <div className="invest-evidence">
        <div className="lab">근거: 이 기사</div>
        {event.summary && <blockquote>{quoted(event.summary)}</blockquote>}
        <div className="meta">
          {[event.publisher, fmtDate(event.dateline)].filter(Boolean).join(" · ")}
          {event.originalUrl && (
            <>
              {" · "}
              <a className="event-link" href={event.originalUrl} target="_blank" rel="noopener noreferrer">
                원문 보기 ↗
              </a>
            </>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="invest-evidence">
      <div className="lab">근거: 관계를 말한 기사 문장</div>
      {node.path.map((step, i) => (
        <div key={i} className="invest-step">
          <div className="meta">
            {step.sourceName ?? "?"} → {step.targetName ?? "?"}
          </div>
          {step.evidence.length ? (
            step.evidence.slice(0, 2).map((e) => (
              <div key={e.bigkindsNewsId + e.snippet.slice(0, 12)}>
                <blockquote>{quoted(e.snippet || e.title)}</blockquote>
                <div className="meta">
                  {[e.publisher, fmtDate(e.publishedAt), afterNews(e.publishedAt, event.dateline) ? AFTER_NEWS : null].filter(Boolean).join(" · ")}
                  {e.originalUrl && (
                    <>
                      {" · "}
                      <a className="event-link" href={e.originalUrl} target="_blank" rel="noopener noreferrer">
                        원문 보기 ↗
                      </a>
                    </>
                  )}
                </div>
              </div>
            ))
          ) : (
            <p className="meta">{NO_EVIDENCE[step.edgeSource ?? "NEWS"] ?? NO_EVIDENCE.NEWS}</p>
          )}
        </div>
      ))}
    </div>
  );
}
