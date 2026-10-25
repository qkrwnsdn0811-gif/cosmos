import { useQueries } from "@tanstack/react-query";
import { api } from "@/api";
import { CompanyAvatar } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { useWatchlist } from "@/lib/queries";
import { useInvest } from "@/store/invest";
import { signalFor, signalText, toneOf } from "./investCopy";

/** 알림에 올리는 관심 종목 수 — 목록이 길어도 첫 화면을 덮지 않게 */
const MAX_WATCHED = 6;
/** 가장 최근 뉴스 날짜에서 이 기간 안의 뉴스만 '새 뉴스'로 본다 (데이터가 실시간이 아니라 오늘 날짜 대신 최근 뉴스 날짜를 기준으로 한다) */
const FRESH_DAYS = 7;

/**
 * 관심 종목 알림(F14) — 로그인 사용자의 관심 종목이 최근 뉴스의 영향권에 들어오면(기사 언급 또는 공급망 추정) 피드 위에 알린다.
 * 기존 관심 목록(/users/me/watch-companies)과 종목별 뉴스 이력을 그대로 쓰고, 알림을 누르면 그 뉴스를 연다.
 */
export default function WatchAlerts({ onPick }: { onPick: (eventId: string) => void }) {
  const horizon = useInvest((s) => s.horizon);
  const watchlist = useWatchlist();
  const watched = (watchlist.data?.items ?? []).slice(0, MAX_WATCHED);
  const histories = useQueries({
    queries: watched.map((w) => ({
      queryKey: ["events", "company-history", w.companyId, "latest"],
      queryFn: () => api.events.companyHistory({ companyId: w.companyId, size: 1 }),
      staleTime: 5 * 60_000,
    })),
  });
  const latest = histories
    .map((h, i) => ({ company: watched[i], item: h.data?.items[0] }))
    .filter((x): x is { company: (typeof watched)[number]; item: NonNullable<typeof x.item> } => Boolean(x.item));
  if (!latest.length) return null;
  const newest = Math.max(...latest.map((x) => new Date(x.item.event.dateline).getTime()));
  const fresh = latest
    .filter((x) => newest - new Date(x.item.event.dateline).getTime() <= FRESH_DAYS * 86_400_000)
    .sort((a, b) => b.item.event.dateline.localeCompare(a.item.event.dateline));
  if (!fresh.length) return null;

  return (
    <section className="invest-watch" aria-label="관심 종목 알림">
      <span className="kick">관심 종목 알림</span>
      <div className="invest-watch-list">
        {fresh.map(({ company, item }) => {
          const signal = signalFor(item, horizon);
          return (
            <button key={company.companyId} type="button" className="invest-watch-item" onClick={() => onPick(item.event.eventId)}>
              <CompanyAvatar name={company.name} companyId={company.companyId} stockCode={company.stockCode} market={company.market} size={22} radius={11} />
              <span className="grow">
                <b>{company.name}</b>
                <span className="meta">
                  {" "}
                  {item.positionLabel ?? (item.basis === "REPORTED" ? "기사에 나옴" : "공급망 추정")} · {fmtDate(item.event.dateline)}
                </span>
                <span className="invest-watch-title clamp-1">{item.event.title}</span>
              </span>
              <span className={`invest-badge ${toneOf(signal?.label)}`}>{signalText(signal ? { ...signal, holdReason: null } : null)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
