import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { CompanyQuote, Market } from "@/api/types";
import { useCompanyQuotes, useLatestGraph, useMarketIndices, useToggleWatch, useWatchedSet, useWatchlist } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { CompanyAvatar, Empty, ErrorNotice, Icon, Skeleton } from "@/components/ui";
import { companyPath } from "@/lib/routes";
import MarketStrip from "./MarketStrip";
import OwnershipView from "./OwnershipView";
import QuoteTable, { type SortState } from "./QuoteTable";
import WatchStrip from "./WatchStrip";
import { PRESETS, SIZE_LABEL, applyPreset, fmtPct, sortQuotes, tone, type PresetKey } from "./quoteFormat";
import "./companies.css";

const MARKETS: { v: Market | ""; label: string }[] = [
  { v: "", label: "전체 시장" },
  { v: "KOSPI", label: "코스피" },
  { v: "KOSDAQ", label: "코스닥" },
];

/** "2026-10-23" → "10월 23일" */
const dayLabel = (iso: string | null | undefined) => (iso ? `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일` : "-");

/**
 * 기업 탭 — 반도체 종목 시세판 (토스증권 '주식 골라보기' + Google Finance 시장 요약을 참고).
 * 시장 띠 → 골라보기 칩 → 필터 → 정렬되는 시세 표. 행을 누르면 기업 상세(/companies/:id). '투자·지분 관계' 보기는 그대로 둔다.
 */
export default function CompaniesPage() {
  const navigate = useNavigate();
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const [params] = useSearchParams();
  const [view, setView] = useState<"list" | "ownership">("list");
  const [keyword, setKeyword] = useState("");
  const [market, setMarket] = useState<Market | "">("");
  const [industry, setIndustry] = useState("");
  const [size, setSize] = useState("");
  const [preset, setPreset] = useState<PresetKey>("all");
  // 열 머리를 누르면 칩의 정렬 대신 이 정렬을 쓴다. 칩을 바꾸면 풀린다
  const [sort, setSort] = useState<SortState | null>(null);

  // 예전 주소(?company=, 영향 종목 카드의 "이 종목의 뉴스 이력")는 기업 상세로 넘긴다
  useEffect(() => {
    const id = params.get("company");
    if (id) navigate(companyPath(id), { replace: true });
  }, [params, navigate]);

  const quotes = useCompanyQuotes();
  const indices = useMarketIndices("3M");
  const universe = useLatestGraph(null);
  const watchlist = useWatchlist();
  const toggleWatch = useToggleWatch();
  const watchedSet = useWatchedSet();

  // 시세판은 주가가 있는 종목만 (주가 없는 기업은 국내 범위 밖의 나스닥 기업들이다 — 2026-10-18 국내 종목만으로 정함)
  const all = useMemo(() => (quotes.data?.items ?? []).filter((q) => q.close != null), [quotes.data]);
  const byId = useMemo(() => new Map((quotes.data?.items ?? []).map((q) => [q.companyId, q])), [quotes.data]);
  const industries = useMemo(() => [...new Set(all.map((q) => q.industry).filter((v): v is string => Boolean(v)))].sort((a, b) => a.localeCompare(b, "ko")), [all]);

  const picked = useMemo(() => {
    const k = keyword.trim().toLowerCase();
    const filtered = all.filter(
      (q) =>
        (!k || q.name.toLowerCase().includes(k) || (q.stockCode ?? "").includes(k)) &&
        (!market || q.market === market) &&
        (!industry || q.industry === industry) &&
        (!size || q.sizeClass === size),
    );
    const byPreset = applyPreset(filtered, preset);
    return sort ? { rows: sortQuotes(byPreset.rows, sort.key, sort.dir), sort } : { rows: byPreset.rows, sort: { key: byPreset.sort, dir: byPreset.dir } };
  }, [all, keyword, market, industry, size, preset, sort]);

  const open = (id: string) => navigate(companyPath(id));
  const onToggleWatch = (companyId: string, watched: boolean) => {
    if (!authed) return openAuth("login");
    toggleWatch.mutate({ companyId, watched });
  };
  const asOf = quotes.data?.asOf ?? null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <span className="pill">기업 · 시세</span>
          <h1>반도체 종목 시세</h1>
          <p>뉴스·관계망으로 연결된 반도체 종목의 주가와 투자 지표입니다. 실시간 시세가 아니라 마지막 거래일 종가 기준입니다.</p>
        </div>
        <span className="status-line">
          <i className="dot" />
          {asOf ? `주가 ${asOf} 종가 기준` : quotes.isError ? "시세를 불러오지 못했습니다" : "시세 확인 중"}
        </span>
      </div>

      {/* 지수를 못 받으면 지수 카드만 빠지고 반도체 등락 카드는 남는다 */}
      {all.length > 0 && !indices.isLoading ? (
        <MarketStrip indices={indices.data?.items ?? []} rows={all} asOf={asOf} />
      ) : (
        (indices.isLoading || quotes.isLoading) && <Skeleton h={118} style={{ marginTop: 18, borderRadius: 18 }} />
      )}

      {all.length > 0 && (
        <WatchStrip
          rows={all}
          onOpen={open}
          onShowAll={() => {
            setView("list");
            setPreset("watch");
            setSort(null);
            requestAnimationFrame(() => document.querySelector(".dir-card")?.scrollIntoView({ behavior: "smooth", block: "start" }));
          }}
        />
      )}

      {/* 관심 기업 — 로그인했을 때만 */}
      {authed && (
        <section className="card watch-card" aria-label="내 관심 기업">
          <div className="row between wrap">
            <div>
              <div className="kick">my watchlist</div>
              <h2 className="sect" style={{ marginTop: 6, fontSize: 20 }}>
                내 관심 기업 <span className="num" style={{ color: "var(--primary-2)" }}>{watchlist.data?.items.length ?? 0}</span>
              </h2>
            </div>
            <span className="meta">프로필 메뉴 › 내 페이지에서 관리</span>
          </div>
          {watchlist.data?.items.length ? (
            <div className="watch-grid">
              {watchlist.data.items.map((w) => {
                const q = byId.get(w.companyId);
                return (
                  <button key={w.companyId} type="button" className="watch-item" onClick={() => open(w.companyId)}>
                    <CompanyAvatar name={w.name} stockCode={w.stockCode} market={w.market} industry={w.primaryIndustry?.name} size={40} />
                    <span className="grow">
                      <b>{w.name}</b>
                      <span className="meta num">
                        {q?.close != null ? `${q.close.toLocaleString("ko-KR")}원 ` : ""}
                        {q && <span className={`tone-${tone(q.change1d)}`}>{fmtPct(q.change1d)}</span>}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : (
            <Empty>아직 관심 기업이 없습니다. 아래 표의 ★ 을 눌러 추가하세요.</Empty>
          )}
        </section>
      )}

      <section className="card dir-card" aria-label="종목 시세">
        <div className="dir-head">
          <div>
            <div className="kick">stock board</div>
            <h2 className="sect" style={{ marginTop: 6 }}>
              {view === "list" ? "종목 시세" : "투자·지분 관계"}
            </h2>
            <p className="meta mt-8" style={{ fontSize: 13 }}>
              {view === "list"
                ? "등락은 거래일 기준(1개월 = 20거래일)입니다. 관계는 공개 은하 스냅샷의 1년 관계 수입니다."
                : "OpenDART 타법인 출자 현황(공시)의 지분율과 뉴스에서 확인된 지분 관계를 보유 → 피보유 방향으로 표시합니다."}
            </p>
          </div>
          <div className="row" style={{ gap: 12 }}>
            <div className="seg">
              <button type="button" className={view === "list" ? "on" : ""} onClick={() => setView("list")}>
                시세
              </button>
              <button type="button" className={view === "ownership" ? "on" : ""} onClick={() => setView("ownership")}>
                투자·지분 관계
              </button>
            </div>
            <span className="pill num">종목 {all.length}개</span>
          </div>
        </div>

        {view === "ownership" ? (
          <OwnershipView graph={universe.data ?? null} onPick={open} />
        ) : (
          <>
            <div className="preset-row" role="group" aria-label="골라보기">
              {PRESETS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  className={`chip ${preset === p.key ? "on" : ""}`}
                  aria-pressed={preset === p.key}
                  onClick={() => {
                    setPreset(p.key);
                    setSort(null);
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="dir-tools">
              <label className="field">
                <Icon.Search />
                <input placeholder="기업명·종목코드" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
              </label>
              <select value={market} onChange={(e) => setMarket(e.target.value as Market | "")} aria-label="시장">
                {MARKETS.map((m) => (
                  <option key={m.v} value={m.v}>
                    {m.label}
                  </option>
                ))}
              </select>
              <select value={industry} onChange={(e) => setIndustry(e.target.value)} aria-label="산업">
                <option value="">전체 산업</option>
                {industries.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <select value={size} onChange={(e) => setSize(e.target.value)} aria-label="규모">
                <option value="">전체 규모</option>
                {Object.entries(SIZE_LABEL).map(([v, label]) => (
                  <option key={v} value={v}>
                    {label}
                  </option>
                ))}
              </select>
              <span className="meta" style={{ marginLeft: "auto" }}>
                {picked.rows.length}종목
              </span>
            </div>

            {quotes.isLoading && (
              <div style={{ padding: "12px 22px" }}>
                {[0, 1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} h={44} style={{ marginBottom: 8 }} />
                ))}
              </div>
            )}
            {quotes.isError && <ErrorNotice message="시세를 불러오지 못했습니다." onRetry={() => quotes.refetch()} />}
            {quotes.data && !picked.rows.length && <Empty>조건에 맞는 종목이 없습니다.</Empty>}
            {picked.rows.length > 0 && (
              <QuoteTable rows={picked.rows} sort={picked.sort} onSort={setSort} watched={(id) => watchedSet.has(id)} onToggleWatch={onToggleWatch} onOpen={open} />
            )}
            <div className="dir-foot">
              <span className="meta">
                행을 누르면 종목 상세로 이동합니다. 시가총액·PER·PBR·배당은 KRX 상장 정보와 OpenDART 재무제표로 계산한 값입니다
                {firstPriceDate(all) ? ` (시가총액 기준 ${dayLabel(firstPriceDate(all))})` : ""}.
              </span>
            </div>
          </>
        )}
      </section>
    </div>
  );
}

/** 지표의 주가 기준일 (모든 종목이 같은 날로 계산된다) */
function firstPriceDate(rows: CompanyQuote[]): string | null {
  return rows.find((r) => r.priceDate)?.priceDate ?? null;
}
