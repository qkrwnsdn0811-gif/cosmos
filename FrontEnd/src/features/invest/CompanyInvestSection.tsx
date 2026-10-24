import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Kpi, Skeleton } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { useCompanyFundamental, useCompanyHistory } from "@/lib/queries";
import { useInvest } from "@/store/invest";
import { fmtPct, fmtWon, HORIZONS, SIZE_LABEL, signalFor, signalText, toneOf } from "./investCopy";
import "./invest.css";

/**
 * 기업 화면의 투자 정보 — 기업 지표(F8)와 이 종목에 영향을 준 과거 뉴스·그때의 주가 반응(F13).
 * 뉴스를 누르면 첫 화면에서 그 뉴스의 영향 종목 카드를 연다.
 */
export default function CompanyInvestSection({ companyId }: { companyId: string }) {
  const horizon = useInvest((s) => s.horizon);
  const fundamental = useCompanyFundamental(companyId);
  const history = useCompanyHistory(companyId);
  const items = useMemo(() => history.data?.pages.flatMap((p) => p.items) ?? [], [history.data]);
  const f = fundamental.data;
  const days = HORIZONS[horizon].days;

  return (
    <section className="invest-company" aria-label="투자 정보">
      <div className="row wrap" style={{ gap: 10 }}>
        <Kpi label="규모" value={f?.sizeClass ? SIZE_LABEL[f.sizeClass] : "-"} unit={f?.marketCapRank ? `시총 ${f.marketCapRank}위` : undefined} />
        <Kpi label="시가총액" value={fmtWon(f?.marketCap)} />
        <Kpi label="PER" value={f?.per != null ? f.per.toFixed(1) : f?.netIncomeTtm != null && f.netIncomeTtm <= 0 ? "적자" : "-"} />
        <Kpi label="PBR" value={f?.pbr != null ? f.pbr.toFixed(2) : "-"} />
        <Kpi label="배당수익률" value={f?.dividendYield != null ? fmtPct(f.dividendYield, 1, false) : "-"} />
      </div>
      <div className="meta mt-8">
        {f?.priceDate ? `${f.priceDate} 종가 기준` : ""}
        {f?.fiscalBasis ? ` · 순이익 ${f.fiscalBasis}` : f && f.per == null && f.pbr == null ? " · 재무 지표(PER·PBR·배당)는 OpenDART 재무제표를 불러온 뒤 표시됩니다" : ""}
      </div>

      <div className="row between mt-16">
        <span className="kick">이 종목에 영향을 준 뉴스</span>
        <span className="meta">{HORIZONS[horizon].label} · 뉴스 뒤 {days}거래일 실제 초과수익률</span>
      </div>
      <div className="invest-history">
        {history.isLoading && [0, 1, 2].map((i) => <Skeleton key={i} h={54} style={{ borderRadius: 10 }} />)}
        {!history.isLoading && !items.length && <p className="meta">이 종목이 영향권에 든 뉴스가 아직 없습니다.</p>}
        {items.map((it) => {
          const signal = signalFor(it, horizon);
          const realized = it.abnormalReturns.find((r) => r.windowDays === days)?.abnormalReturn ?? null;
          return (
            <Link key={it.event.eventId} className="invest-history-item" to={`/?event=${it.event.eventId}`}>
              <span className="row between" style={{ gap: 8 }}>
                <span className="meta">
                  {fmtDate(it.event.dateline)} · {it.positionLabel ?? (it.basis === "REPORTED" ? "기사에 나온 기업" : "공급망 추정")}
                  {it.event.newsTypeLabel ? ` · ${it.event.newsTypeLabel}` : ""}
                </span>
                <span className={`num invest-history-ar ${realized != null && realized > 0 ? "pos" : realized != null && realized < 0 ? "neg" : ""}`}>
                  실제 {fmtPct(realized)}
                </span>
              </span>
              <span className="clamp-1 invest-history-title">{it.event.title}</span>
              <span className={`invest-badge sm ${toneOf(signal?.label)}`}>{signalText(signal ? { ...signal, holdReason: null } : null)}</span>
            </Link>
          );
        })}
        {history.hasNextPage && (
          <button type="button" className="more-btn" onClick={() => history.fetchNextPage()} disabled={history.isFetchingNextPage}>
            {history.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
          </button>
        )}
      </div>
    </section>
  );
}
