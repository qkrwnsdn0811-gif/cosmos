import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { EventMap, EventNode, EventSummary, Horizon, NewsTypeCode } from "@/api/types";
import { CompanyAvatar, Empty, ErrorNotice, Skeleton } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import { useEventFeed, useEventMap } from "@/lib/queries";
import { eventMapPath } from "@/lib/routes";
import { useInvest } from "@/store/invest";
import AccuracyBanner from "./AccuracyBanner";
import { countsText, DISCLAIMER, HORIZONS, NEWS_TYPES } from "./investCopy";
import StockCard from "./StockCard";
import WatchAlerts from "./WatchAlerts";
import "./invest.css";

/**
 * 첫 화면(F10) — 반도체 뉴스를 고르면 영향 받을 종목을 카드로 보여 준다.
 * 왼쪽은 최근 뉴스와 뉴스별 딱지 요약, 오른쪽은 고른 뉴스의 종목 카드(F5). 단기·장기 토글(F9)이 둘 다 바꾼다.
 * 3D 관계 은하는 탐색 화면(/galaxy)으로, 뉴스 1건의 3D 관계 지도는 /events 로 이어진다.
 */
export default function InvestPage() {
  const [params, setParams] = useSearchParams();
  const horizon = useInvest((s) => s.horizon);
  const newsType = (params.get("type") as NewsTypeCode | null) ?? undefined;
  const selectedId = params.get("event");

  const feed = useEventFeed({ valueChain: "SEMICONDUCTOR", newsType });
  const items = useMemo(() => feed.data?.pages.flatMap((p) => p.items) ?? [], [feed.data]);
  const settled = !feed.isPlaceholderData;
  const activeId = selectedId ?? (settled ? items[0]?.eventId ?? null : null);
  const map = useEventMap(activeId);

  const update = (mutate: (next: URLSearchParams) => void, replace = false) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      mutate(next);
      return next;
    }, { replace });
  useEffect(() => {
    if (!selectedId && settled && items[0]) update((n) => n.set("event", items[0].eventId), true);
    // update 는 렌더마다 새로 만들어지지만 setParams 만 감싼다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, selectedId, settled]);

  return (
    <div className="page invest-page">
      <div className="invest-top">
        <div>
          <div className="kick">반도체 뉴스 → 영향 종목</div>
          <h1 className="invest-h1">이 뉴스, 내 종목에는 어떤 일이 있었을까?</h1>
          <p className="meta">기사에 나온 기업과 공급망으로 이어진 기업을, 과거 비슷한 뉴스 뒤 실제 주가 반응과 함께 보여 줍니다.</p>
        </div>
        <HorizonToggle />
      </div>
      <AccuracyBanner horizon={horizon} />
      <WatchAlerts onPick={(eventId) => update((n) => n.set("event", eventId))} />

      <div className="invest-grid">
        <section className="card invest-feed" aria-label="최근 뉴스">
          <div className="row between" style={{ gap: 8 }}>
            <div className="kick">최근 뉴스</div>
            <label className="invest-filter">
              <span className="sr-only">뉴스 유형</span>
              <select
                value={newsType ?? ""}
                onChange={(e) =>
                  update((n) => {
                    if (e.target.value) n.set("type", e.target.value);
                    else n.delete("type");
                    n.delete("event");
                  })
                }
              >
                <option value="">모든 유형</option>
                {NEWS_TYPES.map((t) => (
                  <option key={t.code} value={t.code}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="invest-feed-list">
            {feed.isLoading && [0, 1, 2, 3].map((i) => <Skeleton key={i} h={92} style={{ borderRadius: 14 }} />)}
            {!feed.isLoading && feed.isError && !items.length && <ErrorNotice message="뉴스를 불러오지 못했습니다." onRetry={() => feed.refetch()} />}
            {!feed.isLoading && !feed.isError && !items.length && <Empty>이 유형의 뉴스가 없습니다.</Empty>}
            {items.map((e) => (
              <NewsItem key={e.eventId} event={e} horizon={horizon} active={e.eventId === activeId} onPick={() => update((n) => n.set("event", e.eventId))} />
            ))}
            {feed.hasNextPage && (
              <button type="button" className="more-btn" onClick={() => feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
                {feed.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
              </button>
            )}
          </div>
        </section>

        <section className="invest-detail" aria-label="뉴스 상세">
          {map.isError ? (
            <div className="card">
              <ErrorNotice message="선택한 뉴스를 불러오지 못했습니다." onRetry={() => map.refetch()} />
            </div>
          ) : map.data ? (
            <NewsDetail key={map.data.event.eventId} map={map.data} horizon={horizon} />
          ) : (
            <Skeleton h={420} style={{ borderRadius: 18 }} />
          )}
        </section>
      </div>
      <p className="invest-disclaimer">{DISCLAIMER}</p>
    </div>
  );
}

function HorizonToggle() {
  const horizon = useInvest((s) => s.horizon);
  const setHorizon = useInvest((s) => s.setHorizon);
  return (
    <div className="invest-toggle" role="group" aria-label="투자 기간">
      <span className="lab">투자 기간</span>
      {(Object.keys(HORIZONS) as Horizon[]).map((h) => (
        <button key={h} type="button" className={h === horizon ? "on" : ""} aria-pressed={h === horizon} onClick={() => setHorizon(h)} title={HORIZONS[h].hint}>
          {HORIZONS[h].label}
          <small>{HORIZONS[h].days}거래일</small>
        </button>
      ))}
    </div>
  );
}

function NewsItem({ event, horizon, active, onPick }: { event: EventSummary; horizon: Horizon; active: boolean; onPick: () => void }) {
  const counts = event.signalCounts?.[horizon];
  const shown = event.reportedCompanies.slice(0, 3);
  const rest = event.reportedCompanies.length - shown.length;
  return (
    <button type="button" className={`invest-news ${active ? "on" : ""}`} onClick={onPick} aria-current={active || undefined}>
      <span className="row between" style={{ gap: 8 }}>
        {event.newsTypeLabel ? <span className="invest-type">{event.newsTypeLabel}</span> : <span />}
        <span className="meta num">{[event.publisher, fmtDateTime(event.dateline)].filter(Boolean).join(" · ")}</span>
      </span>
      <span className="invest-news-title clamp-2">{event.title}</span>
      <span className="row wrap invest-news-foot">
        <span className="invest-firms">
          {shown.map((c) => (
            <span key={c.companyId} className="invest-firm">
              <CompanyAvatar name={c.name} companyId={c.companyId} stockCode={c.stockCode} market={c.market} size={18} radius={9} />
              {c.name}
            </span>
          ))}
          {rest > 0 && <span className="meta">+{rest}</span>}
        </span>
        {counts && <SignalDots counts={counts} />}
      </span>
    </button>
  );
}

function SignalDots({ counts }: { counts: { up: number; down: number; hold: number } }) {
  const text = countsText(counts);
  if (!text) return null;
  return (
    <span className="invest-dots" title={text}>
      {counts.down > 0 && (
        <span className="down">
          <i />
          하락 {counts.down}
        </span>
      )}
      {counts.up > 0 && (
        <span className="up">
          <i />
          상승 {counts.up}
        </span>
      )}
      {counts.hold > 0 && (
        <span className="hold">
          <i />
          보류 {counts.hold}
        </span>
      )}
    </span>
  );
}

/** 카드 순서: 기사에 나온 기업 → 추정(연결 강한 순) → 연결이 약해 보류된 기업(접어 둔다) */
function orderNodes(nodes: EventNode[]) {
  const strong = nodes.filter((n) => n.status === "OK");
  const weak = nodes.filter((n) => n.status !== "OK");
  return { strong, weak };
}

function NewsDetail({ map, horizon }: { map: EventMap; horizon: Horizon }) {
  const { event } = map;
  const { strong, weak } = useMemo(() => orderNodes(map.nodes), [map.nodes]);
  const [open, setOpen] = useState<Set<string>>(() => new Set(strong[0] ? [strong[0].companyId ?? strong[0].name] : []));
  const [showWeak, setShowWeak] = useState(false);
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const card = (n: EventNode) => {
    const id = n.companyId ?? n.name;
    return <StockCard key={id} node={n} event={event} horizon={horizon} open={open.has(id)} onToggle={() => toggle(id)} />;
  };

  return (
    <>
      <article className="card invest-news-head">
        <div className="row wrap" style={{ gap: 8 }}>
          {event.newsTypeLabel && <span className="invest-type">유형: {event.newsTypeLabel}</span>}
          <span className="meta">{[event.publisher, fmtDateTime(event.dateline)].filter(Boolean).join(" · ")}</span>
        </div>
        <h2 className="invest-news-h">{event.title}</h2>
        {event.summary && <p className="invest-news-summary">{event.summary}</p>}
        <div className="row wrap" style={{ gap: 8 }}>
          {event.originalUrl && (
            <a className="btn btn-g btn-xs" href={event.originalUrl} target="_blank" rel="noopener noreferrer">
              원문 보기 ↗
            </a>
          )}
          <Link className="btn btn-g btn-xs" to={eventMapPath(event.eventId)}>
            관계 자세히 보기 (3D 지도)
          </Link>
        </div>
      </article>

      <div className="row between invest-list-head">
        <div className="kick">
          영향 받을 종목 <b className="num">{strong.length}</b>
        </div>
        <span className="meta">
          {HORIZONS[horizon].hint} 기준 · 카드를 누르면 주가 차트와 근거가 펼쳐집니다
        </span>
      </div>
      <div className="invest-cards">
        {strong.length ? strong.map(card) : <Empty>이 뉴스에서 찾은 상장 기업이 없습니다.</Empty>}
        {weak.length > 0 && (
          <button type="button" className="more-btn" onClick={() => setShowWeak((v) => !v)} aria-expanded={showWeak}>
            {showWeak ? "연결이 약한 기업 접기" : `연결이 약해 판단을 보류한 기업 ${weak.length}곳 더 보기`}
          </button>
        )}
        {showWeak && weak.map(card)}
      </div>
    </>
  );
}
