import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { EventSummary } from "@/api/types";
import { Empty, ErrorNotice, Skeleton } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import { useEventFeed, useEventMap } from "@/lib/queries";
import EventEvidencePanel from "./EventEvidencePanel";
import EventGalaxy from "./EventGalaxy";
import "@/features/news/news.css";
import "./events.css";

const DISCLAIMER =
  "관계·추정 결과와 초과수익률은 투자 권유가 아닙니다. 초과수익률은 이미 관측된 주가 변화이며 미래 주가 예측이 아닙니다.";

/**
 * NEXUS 이벤트 — 빅카인즈 기사 1건을 고르면 그 기사만의 3D 관계 지도(기사 별 · 언급 기업 · 추정 기업 궤도)를 연다.
 * 레이아웃은 뉴스 화면(news.css)의 레일·지도 카드를 그대로 쓰고, 이벤트 전용 부분만 events.css 에 둔다.
 */
export default function EventsPage() {
  const [params, setParams] = useSearchParams();
  const onlyInferred = params.get("all") !== "1";
  const selectedId = params.get("event");

  const feed = useEventFeed({ valueChain: "SEMICONDUCTOR", hasInferred: onlyInferred || undefined });
  const items = useMemo(() => feed.data?.pages.flatMap((p) => p.items) ?? [], [feed.data]);
  // 필터를 바꾸면 새 목록이 올 때까지 이전 목록(keepPreviousData)이 보인다 — 그 사이엔 첫 항목을 자동 선택하지 않는다
  const settled = !feed.isPlaceholderData;
  const activeId = selectedId ?? (settled ? items[0]?.eventId ?? null : null);
  const map = useEventMap(activeId);
  // 고른 기업은 그 이벤트에서만 유효하다 — 다른 이벤트로 바꾸면 자연히 선택이 풀린다
  const [picked, setPicked] = useState<{ eventId: string; companyId: string } | null>(null);
  const selectedCompany = picked && picked.eventId === activeId ? picked.companyId : null;
  // 같은 기업을 다시 고르면(또는 지도에서 Esc) 선택을 풀고 기사 개요로 돌아간다
  const selectCompany = (companyId: string) =>
    activeId && setPicked(selectedCompany === companyId ? null : { eventId: activeId, companyId });

  useEffect(() => {
    if (!selectedId && settled && items[0]) {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        next.set("event", items[0].eventId);
        return next;
      }, { replace: true });
    }
  }, [items, selectedId, settled, setParams]);

  const pick = (eventId: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("event", eventId);
      return next;
    });
  const toggleInferred = () =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (onlyInferred) next.set("all", "1");
      else next.delete("all");
      next.delete("event");
      return next;
    });

  return (
    <div className="page news-viewport events-viewport">
      <div className="row between wrap" style={{ marginBottom: 14 }}>
        <span className="status-line">
          <i className="dot" />
          빅카인즈 반도체 기사 · 근거(기사 언급)와 추정(공급망 전이)을 분리해 보여줍니다
        </span>
      </div>

      <div className="news-page events-page">
        <section className="card news-rail events-rail" aria-label="이벤트 목록">
          <div className="row between">
            <div className="kick">이벤트</div>
            <button type="button" className={`chip chip-sm ${onlyInferred ? "on" : ""}`} aria-pressed={onlyInferred} onClick={toggleInferred}>
              추정 있는 이벤트만
            </button>
          </div>
          <div className="list scroll stack">
            {feed.isLoading && [0, 1, 2, 3].map((i) => <Skeleton key={i} h={96} style={{ borderRadius: 14 }} />)}
            {!feed.isLoading && feed.isError && !items.length && (
              <ErrorNotice message="이벤트를 불러오지 못했습니다." onRetry={() => feed.refetch()} />
            )}
            {!feed.isLoading && !feed.isError && !items.length && <Empty>조건에 맞는 이벤트가 없습니다.</Empty>}
            {items.map((e) => (
              <EventCard key={e.eventId} event={e} active={e.eventId === activeId} onPick={pick} />
            ))}
            {feed.hasNextPage && (
              <button type="button" className="more-btn" onClick={() => feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
                {feed.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
              </button>
            )}
          </div>
        </section>

        <section className="news-analysis-col events-main" aria-label="이벤트 관계 지도">
          <div className="card event-head">
            {map.isError ? (
              <ErrorNotice message="선택한 이벤트를 불러오지 못했습니다." onRetry={() => map.refetch()} />
            ) : (
              <div className="ttl">{map.data?.event.title ?? <Skeleton h={22} w={360} />}</div>
            )}
          </div>
          <div className="event-body">
            <div className="card map-card event-map-card">
              <div className="map-head">
                <div className="kick">관계 지도</div>
              </div>
              <div className="map-stage">
                {map.isError ? (
                  <Empty>이벤트를 찾을 수 없습니다. 목록에서 다른 이벤트를 골라 주세요.</Empty>
                ) : map.data ? (
                  <EventGalaxy map={map.data} selectedId={selectedCompany} onSelect={selectCompany} />
                ) : (
                  <Skeleton h={420} style={{ borderRadius: 14 }} />
                )}
              </div>
            </div>
            {map.data ? (
              <EventEvidencePanel map={map.data} companyId={selectedCompany} />
            ) : map.isError ? null : (
              <Skeleton h={320} style={{ borderRadius: 14 }} />
            )}
          </div>
          <p className="event-disclaimer">{DISCLAIMER}</p>
        </section>
      </div>
    </div>
  );
}

function EventCard({ event, active, onPick }: { event: EventSummary; active: boolean; onPick: (id: string) => void }) {
  const shown = event.reportedCompanies.slice(0, 3);
  const rest = event.reportedCompanies.length - shown.length;
  return (
    <article
      className={`news-card event-card ${active ? "on" : ""}`}
      onClick={() => onPick(event.eventId)}
      role="button"
      tabIndex={0}
      onKeyDown={(k) => k.key === "Enter" && onPick(event.eventId)}
    >
      <div className="row between">
        <span className="lab">
          추정 <b className="num">{event.inferredCount}</b> · 보류 <b className="num">{event.holdCount}</b>
        </span>
        <span className="meta num">{[event.publisher, fmtDateTime(event.dateline)].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="title clamp-2">{event.title}</div>
      <div className="row wrap event-companies">
        {shown.map((c) => (
          <span key={c.companyId} className="chip chip-sm">
            {c.name}
          </span>
        ))}
        {rest > 0 && <span className="meta">+{rest}</span>}
      </div>
    </article>
  );
}
