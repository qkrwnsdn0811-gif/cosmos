import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { CompanyQuote, PricePeriod, RelatedCompany } from "@/api/types";
import { CompanyAvatar, Empty, ErrorNotice, Icon, Skeleton } from "@/components/ui";
import Sparkline from "@/components/Sparkline";
import { useCompanyQuotes, useMarketIndices, useRelatedCompanies, useStockPrices, useToggleWatch, useWatchedSet } from "@/lib/queries";
import { companyPath, galaxyCompanyPath } from "@/lib/routes";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import CompanyNewsHistory from "@/features/invest/CompanyNewsHistory";
import { josa } from "@/features/invest/investCopy";
import RangeBar from "./RangeBar";
import { WatchChips } from "./WatchStrip";
import ReturnChart from "./ReturnChart";
import {
  SIZE_LABEL,
  chartSeries,
  dailyCandles,
  fmtCap,
  fmtPct,
  fmtPrice,
  fmtRatio,
  fmtSigned,
  median,
  perShare,
  perText,
  relationLabel,
  sliceDays,
  tone,
  watchReasons,
} from "./quoteFormat";
import "../invest/invest.css";
import "./companies.css";

const PERIODS: PricePeriod[] = ["1M", "3M", "6M", "1Y"];
const PERIOD_LABEL: Record<PricePeriod, string> = { "1M": "1개월", "3M": "3개월", "6M": "6개월", "1Y": "1년" };
const BENCH: Record<string, string> = { KOSPI: "코스피", KOSDAQ: "코스닥" };

/** "2026-10-23" → "10월 23일" */
const dayLabel = (iso: string | null | undefined) => (iso ? `${Number(iso.slice(5, 7))}월 ${Number(iso.slice(8, 10))}일` : "-");

/**
 * 기업 상세 (토스증권 종목 화면 + Google Finance 종목 화면을 참고).
 * 머리 줄(가격·등락·1일/52주 범위) → 왼쪽: 지수 비교 차트·핵심 수치·투자 지표·같은 업종 비교 / 오른쪽: 관계 기업 동향·최근 뉴스.
 * 숫자는 관측값만 보여 주고, 오른다·내린다·비싸다·싸다는 말하지 않는다.
 */
export default function CompanyPage() {
  const { companyId = "" } = useParams();
  const quotes = useCompanyQuotes();
  const prices = useStockPrices(companyId || null, "1Y");
  const indices = useMarketIndices("1Y");
  const related = useRelatedCompanies(companyId || null);
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const watchedSet = useWatchedSet();
  const toggleWatch = useToggleWatch();
  const [period, setPeriod] = useState<PricePeriod>("3M");
  const [compare, setCompare] = useState(true);
  // 관계 기업·업종 비교에서 다른 종목으로 옮기면 맨 위부터 보여 준다
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [companyId]);

  const q = quotes.data?.items.find((x) => x.companyId === companyId) ?? null;
  // 종목을 옮기면 react-query 가 이전 종목의 일봉을 잠깐 보여 준다 — 그동안은 불러오는 중으로 둔다
  const daily = useMemo(() => dailyCandles(prices.data, companyId), [prices.data, companyId]);
  const pricesLoading = prices.isLoading || prices.isPlaceholderData || (prices.data != null && prices.data.companyId !== companyId);
  const benchCode = q?.market === "KOSDAQ" ? "KOSDAQ" : "KOSPI";
  const benchName = `${BENCH[benchCode]} 지수`;
  const indexPoints = useMemo(() => indices.data?.items.find((i) => i.indexCode === benchCode)?.points ?? [], [indices.data, benchCode]);

  const series = useMemo(() => chartSeries(daily, indexPoints, period, compare), [daily, indexPoints, period, compare]);
  const chartPoints = series.points;

  if (quotes.isLoading) {
    return (
      <div className="page">
        <Skeleton h={120} style={{ borderRadius: 18 }} />
        <Skeleton h={320} style={{ borderRadius: 18, marginTop: 16 }} />
      </div>
    );
  }
  if (quotes.isError) {
    return (
      <div className="page">
        <ErrorNotice message="시세를 불러오지 못했습니다." onRetry={() => quotes.refetch()} />
      </div>
    );
  }
  if (!q) {
    return (
      <div className="page">
        <Empty>
          기업을 찾을 수 없습니다. <Link to="/companies">종목 시세로 돌아가기</Link>
        </Empty>
      </div>
    );
  }

  const last = daily[daily.length - 1] ?? null;
  const year = sliceDays(daily, "1Y");
  const yearLow = year.length ? Math.min(...year.map((d) => d.low)) : null;
  const yearHigh = year.length ? Math.max(...year.map((d) => d.high)) : null;
  const inPeriod = sliceDays(daily, period);
  const avgTurnover = inPeriod.length ? inPeriod.reduce((s, d) => s + d.close * d.volume, 0) / inPeriod.length : null;
  const amount = q.close != null && q.prevClose != null ? q.close - q.prevClose : null;
  const t = tone(q.change1d);
  const watched = watchedSet.has(q.companyId);
  const lastPoint = chartPoints[chartPoints.length - 1];

  return (
    <div className="page cp">
      <Link to="/companies" className="cp-back meta">
        ← 종목 시세
      </Link>

      {/* 머리 줄 (토스) */}
      <section className={`card cp-head ${watchReasons(q).length ? "cp-watch" : ""}`} aria-label={`${q.name} 시세`}>
        {watchReasons(q).length > 0 && (
          <div className="cp-watch-row">
            <span className="watch-badge">주목</span>
            <WatchChips q={q} />
          </div>
        )}
        <div className="cp-id">
          <CompanyAvatar name={q.name} companyId={q.companyId} stockCode={q.stockCode} market={q.market} industry={q.industry} size={48} />
          <div className="cp-title">
            <div className="row wrap" style={{ gap: 8, alignItems: "baseline" }}>
              <h1>{q.name}</h1>
              <span className="meta num">{[q.stockCode, BENCH[q.market ?? ""] ?? q.market, q.industry].filter(Boolean).join(" · ")}</span>
            </div>
            <div className="cp-price">
              <b className="num">{fmtPrice(q.close)}</b>
              <span className={`num tone-${t}`}>
                {fmtSigned(amount)} ({fmtPct(q.change1d)})
              </span>
              <span className="meta">{dayLabel(q.tradingDate)} 종가</span>
            </div>
          </div>
          <div className="cp-actions">
            <button
              type="button"
              className={`btn btn-g btn-sm star-toggle ${watched ? "on" : ""}`}
              onClick={() => (authed ? toggleWatch.mutate({ companyId: q.companyId, watched }) : openAuth("login"))}
              aria-pressed={watched}
            >
              <Icon.Star filled={watched} /> 관심
            </button>
            <Link className="btn btn-g btn-sm" to={galaxyCompanyPath(q.companyId)}>
              관계 은하에서 보기
            </Link>
          </div>
        </div>
        <div className="cp-ranges">
          <RangeBar label="1일 범위" low={last?.low ?? null} high={last?.high ?? null} value={q.close} />
          <RangeBar label="52주 범위" low={yearLow} high={yearHigh} value={q.close} />
          <div className="cp-mini">
            <span className="rb-label">시가총액</span>
            <b className="num">{fmtCap(q.marketCap)}</b>
            {q.marketCapRank && <span className="meta">반도체 {q.marketCapRank}위</span>}
          </div>
          <div className="cp-mini">
            <span className="rb-label">거래대금</span>
            <b className="num">{last ? fmtCap(last.close * last.volume) : "-"}</b>
            <span className="meta">종가×거래량</span>
          </div>
        </div>
      </section>

      <div className="cp-grid">
        <div className="cp-col cp-main">
          {/* 차트 (Google Finance) */}
          <section className="card cp-card cp-chart" aria-label="주가 차트">
            <div className="row between wrap" style={{ gap: 10 }}>
              <div className="seg" role="group" aria-label="기간">
                {PERIODS.map((p) => (
                  <button key={p} type="button" className={period === p ? "on" : ""} onClick={() => setPeriod(p)}>
                    {p}
                  </button>
                ))}
              </div>
              <label className="cp-compare">
                <input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> {benchName}와 비교
              </label>
            </div>
            {pricesLoading || (compare && indices.isLoading) ? (
              <Skeleton h={240} style={{ borderRadius: 12, marginTop: 12 }} />
            ) : daily.length < 2 ? (
              <p className="invest-muted">주가 데이터가 없습니다.</p>
            ) : (
              <>
                {series.indexMissing && <p className="meta cp-note">지수 데이터를 불러오지 못해 주가만 보여 줍니다.</p>}
                <ReturnChart points={chartPoints} mode={series.mode} name={q.name} benchName={benchName} />
                {lastPoint && (
                  <p className="cp-summary num">
                    {PERIOD_LABEL[period]} <b className={`tone-${tone(lastPoint.stockReturn)}`}>{fmtPct(lastPoint.stockReturn, 1)}</b>
                    {series.mode === "compare" && (
                      <>
                        {" · "}
                        {BENCH[benchCode]} <b className={`tone-${tone(lastPoint.indexReturn)}`}>{fmtPct(lastPoint.indexReturn, 1)}</b>
                        {" · 차이 "}
                        <b>{fmtGap(lastPoint.stockReturn - lastPoint.indexReturn)}</b>
                      </>
                    )}
                  </p>
                )}
              </>
            )}
          </section>

          {/* 핵심 수치 (Google Finance) */}
          <section className="card cp-card cp-stats" aria-label="핵심 수치">
            <Stat label="시가" value={fmtPrice(last?.open)} />
            <Stat label="고가" value={fmtPrice(last?.high)} />
            <Stat label="저가" value={fmtPrice(last?.low)} />
            <Stat label="거래량" value={last ? `${last.volume.toLocaleString("ko-KR")}주` : "-"} />
            <Stat label={`평균 거래대금 (${PERIOD_LABEL[period]})`} value={fmtCap(avgTurnover)} />
            <Stat label="52주 최고" value={fmtPrice(yearHigh)} />
            <Stat label="52주 최저" value={fmtPrice(yearLow)} />
            <Stat label="시가총액" value={fmtCap(q.marketCap)} sub={q.sizeClass ? `${SIZE_LABEL[q.sizeClass]}${q.marketCapRank ? ` · ${q.marketCapRank}위` : ""}` : undefined} />
          </section>

          <Metrics q={q} peers={quotes.data?.items ?? []} />
          <IndustryCompare q={q} peers={quotes.data?.items ?? []} />
        </div>

        <div className="cp-col cp-side">
          {/* 종목이 바뀌면 '모두 보기' 펼침을 처음 상태로 */}
          <RelatedTrend key={q.companyId} loading={related.isLoading} error={related.isError} items={related.data?.items ?? []} />
          <section className="card cp-card cp-news" aria-label="최근 뉴스">
            <h2 className="cp-h">최근 뉴스</h2>
            <CompanyNewsHistory companyId={q.companyId} />
          </section>
        </div>
      </div>
    </div>
  );
}

/** 지수 대비 차이 → "+8.2%p" */
function fmtGap(gap: number): string {
  return fmtPct(gap, 1).replace("%", "%p");
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="cp-stat">
      <span className="rb-label">{label}</span>
      <b className="num">{value}</b>
      {sub && <span className="meta">{sub}</span>}
    </div>
  );
}

/** 같은 대표 업종의 주가 있는 종목 */
function industryPeers(q: CompanyQuote, peers: CompanyQuote[]): CompanyQuote[] {
  return peers.filter((p) => p.industry && p.industry === q.industry && p.close != null);
}

/** 투자 지표 세 묶음 (토스: 가치평가 · 수익 · 배당) */
function Metrics({ q, peers }: { q: CompanyQuote; peers: CompanyQuote[] }) {
  const same = industryPeers(q, peers);
  const perMedian = median(same.map((p) => (p.per != null && p.per > 0 ? p.per : null)));
  const pbrMedian = median(same.map((p) => p.pbr));
  const { eps, bps, roe } = perShare(q);
  return (
    <section className="card cp-card" aria-label="투자 지표">
      <div className="row between wrap">
        <h2 className="cp-h">투자 지표</h2>
        <span className="meta">{[q.priceDate && `주가 ${q.priceDate}`, q.fiscalBasis && `재무 ${q.fiscalBasis}`].filter(Boolean).join(" · ")}</span>
      </div>
      <div className="cp-metrics">
        <div className="cp-group">
          <span className="kick">가치평가</span>
          <Metric label="PER" value={perText(q)} sub={perMedian != null ? `${q.industry} 중앙값 ${perMedian.toFixed(1)}배` : undefined} />
          <Metric label="PBR" value={q.pbr != null ? `${q.pbr.toFixed(2)}배` : "-"} sub={pbrMedian != null ? `${q.industry} 중앙값 ${pbrMedian.toFixed(2)}배` : undefined} />
        </div>
        <div className="cp-group">
          <span className="kick">수익</span>
          <Metric label="순이익 (최근 4분기)" value={fmtCap(q.netIncomeTtm)} />
          <Metric label="EPS" value={eps != null ? fmtPrice(eps) : "-"} sub="계산값" />
          <Metric label="BPS" value={bps != null ? fmtPrice(bps) : "-"} sub="계산값" />
          <Metric label="ROE" value={roe == null ? "-" : roe < 0 ? fmtPct(roe, 1) : fmtRatio(roe, 1)} sub="계산값" />
        </div>
        <div className="cp-group">
          <span className="kick">배당</span>
          <Metric label="주당 배당금" value={q.dividendPerShare ? fmtPrice(q.dividendPerShare) : "-"} />
          <Metric label="배당수익률" value={q.dividendYield ? fmtRatio(q.dividendYield) : "-"} />
        </div>
      </div>
      <p className="meta cp-note">EPS·BPS 는 상장주식수(시가총액 ÷ 종가)로, ROE 는 순이익 ÷ 자본총계로 계산한 값입니다.</p>
    </section>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="cp-metric">
      <span>{label}</span>
      <b className="num">{value}</b>
      {sub && <em>{sub}</em>}
    </div>
  );
}

/** 같은 업종 비교 (토스 '다른 종목과 비교하기'): PER 낮은 순, 적자·값 없음은 뒤, 업종 중앙값 줄을 끼운다 */
function IndustryCompare({ q, peers }: { q: CompanyQuote; peers: CompanyQuote[] }) {
  const same = industryPeers(q, peers);
  if (same.length < 2) return null;
  const perOf = (p: CompanyQuote) => (p.per != null && p.per > 0 ? p.per : null);
  const sorted = [...same].sort((a, b) => {
    const pa = perOf(a);
    const pb = perOf(b);
    if (pa == null && pb == null) return (b.marketCap ?? 0) - (a.marketCap ?? 0);
    if (pa == null) return 1;
    if (pb == null) return -1;
    return pa - pb;
  });
  const med = median(same.map(perOf));
  const mine = perOf(q);
  const sentence =
    mine == null
      ? `${q.name}${josa(q.name, "은", "는")} ${perText(q) === "적자" ? "적자라" : "PER 값이 없어"} ${q.industry} 업종과 PER을 비교하지 않습니다.`
      : med == null
        ? ""
        : `${q.industry} 업종 중앙값보다 PER이 ${mine < med ? "낮아요" : mine > med ? "높아요" : "같아요"} (${mine.toFixed(1)}배 · 중앙값 ${med.toFixed(1)}배)`;
  const medianAt = med == null ? -1 : sorted.findIndex((p) => perOf(p) == null || perOf(p)! > med);
  const rows: (CompanyQuote | "median")[] = [...sorted];
  if (med != null) rows.splice(medianAt < 0 ? sorted.length : medianAt, 0, "median");
  return (
    <section className="card cp-card" aria-label="같은 업종 비교">
      <h2 className="cp-h">같은 업종 비교 · {q.industry}</h2>
      {sentence && <p className="cp-sentence">{sentence}</p>}
      <div style={{ overflowX: "auto" }}>
        <table className="dir-table qt cp-peers">
          <thead>
            <tr>
              <th>기업</th>
              <th className="right">PER</th>
              <th className="right">시가총액</th>
              <th className="right">주가</th>
              <th className="right">1개월</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) =>
              p === "median" ? (
                <tr key="median" className="cp-median">
                  <td>업종 중앙값</td>
                  <td className="right num">{med!.toFixed(1)}배</td>
                  <td className="right">-</td>
                  <td className="right">-</td>
                  <td className="right">-</td>
                </tr>
              ) : (
                <tr key={p.companyId} className={p.companyId === q.companyId ? "cp-me" : ""}>
                  <td>
                    {p.companyId === q.companyId ? (
                      <b>{p.name}</b>
                    ) : (
                      <Link to={companyPath(p.companyId)}>{p.name}</Link>
                    )}
                  </td>
                  <td className={`right num ${perText(p) === "적자" ? "qt-loss" : ""}`}>{perText(p)}</td>
                  <td className="right num">{fmtCap(p.marketCap)}</td>
                  <td className="right num">{fmtPrice(p.close)}</td>
                  <td className={`right num tone-${tone(p.change1m)}`}>{fmtPct(p.change1m, 1)}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** 관계 기업 동향 (Google '관련 주식' 자리, NEXUS 의 관계 그래프로 고른 종목) */
/** 처음에 보여 줄 관계 기업 수 — 나머지는 '모두 보기' */
const RELATED_FIRST = 8;

function RelatedTrend({ loading, error, items }: { loading: boolean; error: boolean; items: RelatedCompany[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, RELATED_FIRST);
  const priced = items.filter((r) => r.quote.change1m != null);
  const up = priced.filter((r) => (r.quote.change1m ?? 0) > 0).length;
  const down = priced.filter((r) => (r.quote.change1m ?? 0) < 0).length;
  const avg = priced.length ? priced.reduce((s, r) => s + (r.quote.change1m ?? 0), 0) / priced.length : null;
  return (
    <section className="card cp-card cp-related" aria-label="관계 기업 동향">
      <h2 className="cp-h">관계 기업 동향</h2>
      {loading && [0, 1, 2].map((i) => <Skeleton key={i} h={48} style={{ marginTop: 8, borderRadius: 10 }} />)}
      {error && <p className="meta">관계 기업을 불러오지 못했습니다.</p>}
      {!loading && !error && !items.length && <p className="meta">공개 은하 스냅샷에 이 기업의 1년 관계가 없습니다.</p>}
      {items.length > 0 && (
        <>
          <p className="cp-related-sum num">
            관계 기업 {items.length}곳 · 1개월 <span className="tone-rise">상승 {up}</span> · <span className="tone-fall">하락 {down}</span>
            {avg != null && (
              <>
                {" "}
                · 평균 <b className={`tone-${tone(avg)}`}>{fmtPct(avg, 1)}</b>
              </>
            )}
          </p>
          <ul className="cp-related-list">
            {shown.map((r) => (
              <li key={r.quote.companyId}>
                <Link to={companyPath(r.quote.companyId)} className="cp-rel">
                  <CompanyAvatar name={r.quote.name} companyId={r.quote.companyId} stockCode={r.quote.stockCode} market={r.quote.market} industry={r.quote.industry} size={30} />
                  <span className="cp-rel-name">
                    <b>{r.quote.name}</b>
                    <span className="meta">
                      <span className="cp-rel-chip">{relationLabel(r.relationType, r.direction)}</span>
                      {r.otherTypes.length > 0 && ` + ${r.otherTypes.map((o) => relationLabel(o, "NONE")).join(", ")}`}
                    </span>
                  </span>
                  <Sparkline values={r.quote.spark} width={64} height={24} />
                  <span className="cp-rel-num num">
                    <b>
                      {fmtPrice(r.quote.close)} <span className={`tone-${tone(r.quote.change1d)}`}>{fmtPct(r.quote.change1d)}</span>
                    </b>
                    <span className={`tone-${tone(r.quote.change1m)}`}>1개월 {fmtPct(r.quote.change1m, 1)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          {items.length > RELATED_FIRST && (
            <button type="button" className="more-btn" onClick={() => setAll((v) => !v)}>
              {all ? "접기" : `관계 기업 ${items.length}곳 모두 보기`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
