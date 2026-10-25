import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Skeleton } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { useCompanyHistory } from "@/lib/queries";
import { useInvest } from "@/store/invest";
import { fmtPct, HORIZONS, signalFor, signalText, toneOf } from "./investCopy";
import "./invest.css";

/**
 * 이 종목이 영향권에 든 과거 뉴스와 그때의 주가 반응(F13). 기업 상세 페이지의 '최근 뉴스'에 쓴다.
 * 뉴스를 누르면 첫 화면에서 그 뉴스의 영향 종목 카드를 연다.
 */
export default function CompanyNewsHistory({ companyId }: { companyId: string }) {
  const horizon = useInvest((s) => s.horizon);
  const history = useCompanyHistory(companyId);
  const items = useMemo(() => history.data?.pages.flatMap((p) => p.items) ?? [], [history.data]);
  const days = HORIZONS[horizon].days;

  return (
    <div className="invest-history">
      <span className="meta">
        {HORIZONS[horizon].label} · 뉴스 뒤 {days}거래일 실제 초과수익률
      </span>
      {history.isLoading && [0, 1, 2].map((i) => <Skeleton key={i} h={54} style={{ borderRadius: 10 }} />)}
      {!history.isLoading && !items.length && <p className="meta">이 종목이 영향권에 든 뉴스가 아직 없습니다.</p>}
      {items.map((it) => {
        const signal = signalFor(it, horizon);
        const realized = it.abnormalReturns.find((r) => r.windowDays === days)?.abnormalReturn ?? null;
        return (
          <Link key={it.event.eventId} className="invest-history-item" to={`/?event=${it.event.eventId}`}>
            <span className="row between" style={{ gap: 8 }}>
              <span className="meta">
                {fmtDate(it.event.dateline)}
                {it.event.publisher ? ` · ${it.event.publisher}` : ""} · {it.positionLabel ?? (it.basis === "REPORTED" ? "기사에 나온 기업" : "공급망 추정")}
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
  );
}
