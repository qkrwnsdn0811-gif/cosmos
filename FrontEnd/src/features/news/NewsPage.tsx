import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import type { GraphEdge, LatestGraph, LatestGraphNode, NewsDetail, NewsRelatedCompany, RelationshipType, Sentiment } from "@/api/types";
import { fmtDateTime, fmtRelative, josa } from "@/lib/format";
import { SENTIMENTS, SENTIMENT_META, marketLabel, relationshipMeta } from "@/lib/meta";
import { useCompany, useIndustries, useLatestGraph, useNewsDetail, useNewsFeed, useToggleScrap, useToggleWatch } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { CompanyAvatar, Empty, Icon, Kpi, ScoreBar, SentimentBadge, Skeleton } from "@/components/ui";
import KpiEvidenceDialog, { type KpiEvidenceKind } from "./KpiEvidenceDialog";
import NewsDetailDialog from "./NewsDetailDialog";
import RelationMap2D from "./RelationMap2D";
import "./news.css";

/**
 * 뉴스 (화면구성도 ⑦~⑨, 시안 A1). 좌측 컨텍스트 레일 + 우측 관계도.
 * 관계도의 기업 노드를 누르면 좌측 목록이 그 기업 뉴스로 전환되고 브레드크럼이 복귀 경로를 보여준다.
 */
export default function NewsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useUi((s) => s.toast);
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);

  const companyId = params.get("company");
  const [industryId, setIndustryId] = useState<string | null>(null);
  const [sentiment, setSentiment] = useState<Sentiment | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [showDetail, setShowDetail] = useState(false);
  const [kpiModal, setKpiModal] = useState<KpiEvidenceKind | null>(null);

  // 입력마다 요청이 나가지 않도록 디바운스한다 (live 모드에서 백엔드 호출 폭주 방지)
  useEffect(() => {
    const t = setTimeout(() => setKeyword(keywordInput.trim()), 300);
    return () => clearTimeout(t);
  }, [keywordInput]);

  const industries = useIndustries();
  const universe = useLatestGraph(null);
  const company = useCompany(companyId);
  const feed = useNewsFeed({ companyId: companyId ?? undefined, industryId: companyId ? undefined : industryId ?? undefined, sentiment: sentiment ?? undefined, keyword: keyword || undefined, size: 20 });
  const items = useMemo(() => feed.data?.pages.flatMap((p) => p.items) ?? [], [feed.data]);

  // 목록이 바뀌거나(필터 변경 등) 선택된 기사가 새 목록에 없으면 첫 기사를 기본값으로 본다.
  // effect에서 setState로 동기화하면 렌더가 한 번 더 캐스케이드되므로, 렌더링 중 파생값으로 계산한다.
  const activeId = selectedId && items.some((n) => n.newsId === selectedId) ? selectedId : items[0]?.newsId ?? null;

  const detail = useNewsDetail(activeId);
  const toggleScrap = useToggleScrap();
  const toggleWatch = useToggleWatch();

  const setCompany = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("company", id);
    else next.delete("company");
    setParams(next);
    setSentiment(null);
  };

  /* 관계 지도 모델: 선택 뉴스가 직접 언급한 기업들. 1홉 확장은 관련 기업이 많은 뉴스에서 선이 한 점에
   * 뭉치거나 목록이 지나치게 길어지는 문제가 있었고, 무엇보다 '뉴스가 실제로 언급한 기업'만 보는 편이
   * 명확하다는 팀 합의로 기능 자체를 없앴다 — 은하 뷰의 3D 그래프를 여기 다시 그리지 않기로 한 것과
   * 같은 이유다(은하 페이지에 이미 1홉·다홉 탐색이 있다). */
  const seedIds = useMemo(() => {
    const d = detail.data;
    if (!d) return [] as string[];
    const weight = (r: NewsRelatedCompany) => (r.relevanceScore ?? 0) * (r.impactScore ?? 0);
    const sorted = d.relatedCompanies.slice().sort((a, b) => weight(b) - weight(a));
    const ids = sorted.map((r) => r.companyId);
    if (companyId && ids.includes(companyId)) return [companyId, ...ids.filter((x) => x !== companyId)];
    return ids;
  }, [detail.data, companyId]);
  const seedSet = useMemo(() => new Set(seedIds.filter((id) => universe.data?.nodes.some((n) => n.companyId === id))), [seedIds, universe.data]);
  const mapNodes: LatestGraphNode[] = useMemo(() => (universe.data ? universe.data.nodes.filter((n) => seedSet.has(n.companyId)) : []), [universe.data, seedSet]);
  const mapEdges = useMemo(
    () => (universe.data ? universe.data.edges.filter((e) => seedSet.has(e.sourceCompanyId) && seedSet.has(e.targetCompanyId)) : []),
    [universe.data, seedSet],
  );

  const analysis = useMemo(() => (detail.data && universe.data ? analyze(detail.data, universe.data) : null), [detail.data, universe.data]);

  const kpi = detail.data
    ? {
        companies: detail.data.relatedCompanies.length,
        pos: detail.data.relatedCompanies.filter((r) => r.sentiment === "POSITIVE").length,
        neg: detail.data.relatedCompanies.filter((r) => r.sentiment === "NEGATIVE").length,
        evidence: detail.data.evidence.length,
        confidence: detail.data.evidence.length ? Math.round((detail.data.evidence.reduce((s, e) => s + (e.confidence ?? 0), 0) / detail.data.evidence.length) * 100) : null,
      }
    : null;

  /**
   * "주요 경로" 카드는 2홉 이상일 때만 의미가 있는데 실제로는 거의 항상 1홉이라 자리가 비다시피
   * 했다. "왜 이 기업까지 영향이 이어지는지"는 랭킹 카드의 hop≥1 행 아래로 옮기고(각 행 바로
   * 아래 rank-why), 이 카드 자리는 뉴스가 언급한 기업들의 산업·관계 유형 분포로 바꿨다 —
   * 새 API 호출 없이 관계 지도에 이미 쓰던 mapNodes/mapEdges만으로 계산되고, 기사가 짧아도
   * (기업 1~2개라도) 항상 채워진다.
   */
  const industryDist = useMemo(() => {
    const m = new Map<string, number>();
    mapNodes.forEach((n) => m.set(n.industryName, (m.get(n.industryName) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  }, [mapNodes]);
  const relDist = useMemo(() => {
    const m = new Map<RelationshipType, number>();
    mapEdges.forEach((e) => m.set(e.relationshipType, (m.get(e.relationshipType) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => ({ type, count, meta: relationshipMeta(type) }));
  }, [mapEdges]);

  return (
    <div className="page">
      <div className="row between wrap" style={{ marginBottom: 14 }}>
        <span className="status-line">
          <i className="dot" />
          {universe.data ? `스냅샷 ${fmtDateTime(universe.data.asOfAt)} 기준 · 1시간 단위 갱신` : "스냅샷 확인 중"}
        </span>
        <button
          type="button"
          className="btn btn-g btn-sm"
          onClick={() => {
            qc.invalidateQueries({ queryKey: ["news"] });
            qc.invalidateQueries({ queryKey: ["graph", "latest"] });
            toast("최신 뉴스·관계 데이터로 새로고침했습니다.", "success");
          }}
        >
          <Icon.Refresh /> 새로고침
        </button>
      </div>

      <div className="news-page">
        {/* ---------------- 좌측 레일 ---------------- */}
        <section className="card news-rail" aria-label="뉴스 목록">
          {companyId ? (
            <>
              <div className="crumbs">
                <button type="button" onClick={() => setCompany(null)}>
                  전체 뉴스
                </button>
                <span aria-hidden="true">›</span>
                <b>{company.data?.name ?? "…"}</b>
                <button type="button" className="reset" onClick={() => setCompany(null)}>
                  전체로 <Icon.Close />
                </button>
              </div>
              <div className="company-ctx">
                {company.data && <CompanyAvatar name={company.data.name} stockCode={company.data.stockCode} industry={company.data.industries[0]?.name} size={40} />}
                <div className="grow">
                  <div className="nm">{company.data?.name ?? <Skeleton h={18} w={120} />} 뉴스</div>
                  <div className="meta num">
                    {[company.data?.stockCode, company.data?.industries.find((i) => i.primary)?.name, `${items.length}건${feed.hasNextPage ? "+" : ""}`].filter(Boolean).join(" · ")}
                  </div>
                </div>
              </div>
              <label className="field search-field mt-12">
                <Icon.Search />
                <input placeholder="제목으로 뉴스 검색" value={keywordInput} onChange={(e) => setKeywordInput(e.target.value)} aria-label="뉴스 검색" />
              </label>
              <div className="row wrap mt-12" style={{ gap: 7 }}>
                <button type="button" className={`chip chip-sm ${sentiment === null ? "on" : ""}`} onClick={() => setSentiment(null)}>
                  전체
                </button>
                {SENTIMENTS.map((s) => (
                  <button key={s} type="button" className={`chip chip-sm ${sentiment === s ? "on" : ""}`} onClick={() => setSentiment(s)}>
                    <i style={{ background: SENTIMENT_META[s].color }} />
                    {SENTIMENT_META[s].label}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <div className="row between">
                <div>
                  <div className="kick">latest feed</div>
                  <h2 className="sect" style={{ marginTop: 6 }}>
                    최신 금융 뉴스
                  </h2>
                </div>
                <select className="industry-select" value={industryId ?? ""} onChange={(e) => setIndustryId(e.target.value || null)} aria-label="산업 필터">
                  <option value="">전체 산업</option>
                  {industries.data?.items
                    .filter((i) => i.companyCount > 0)
                    .map((i) => (
                      <option key={i.industryId} value={i.industryId}>
                        {i.name}
                      </option>
                    ))}
                </select>
              </div>
              <label className="field search-field mt-12">
                <Icon.Search />
                <input placeholder="제목으로 뉴스 검색" value={keywordInput} onChange={(e) => setKeywordInput(e.target.value)} aria-label="뉴스 검색" />
              </label>
            </>
          )}

          <div className="list scroll stack">
            {feed.isLoading && [0, 1, 2, 3].map((i) => <Skeleton key={i} h={110} style={{ borderRadius: 14 }} />)}
            {!feed.isLoading && !items.length && <Empty>조건에 맞는 뉴스가 없습니다.</Empty>}
            {items.map((n) => (
              <article key={n.newsId} className={`news-card ${n.newsId === activeId ? "on" : ""}`} onClick={() => setSelectedId(n.newsId)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSelectedId(n.newsId)}>
                <div className="row between">
                  <div className="row" style={{ gap: 8 }}>
                    <SentimentBadge value={n.sentiment} />
                    <span className="lab">
                      연결 <b className="num">{n.relatedCompanies.length}</b>
                    </span>
                  </div>
                  <span className="meta num">{[n.publisher, fmtRelative(n.publishedAt)].filter(Boolean).join(" · ")}</span>
                </div>
                <div className="title clamp-2">{n.title}</div>
                <div className="summary clamp-2">{n.summary}</div>
              </article>
            ))}
            {feed.hasNextPage && (
              <button type="button" className="more-btn" onClick={() => feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
                {feed.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
              </button>
            )}
          </div>
        </section>

        {/* ---------------- 우측 ---------------- */}
        <section aria-label="관계 분석">
          <div className="kpi-row">
            <Kpi label="선택 뉴스 연결 기업" value={kpi?.companies ?? "-"} unit="개사" onClick={detail.data ? () => setKpiModal("companies") : undefined} />
            <Kpi
              label="긍정 / 부정 기업"
              value={kpi ? `${kpi.pos} / ${kpi.neg}` : "-"}
              tone={kpi && kpi.neg > kpi.pos ? "var(--neg-fg)" : undefined}
              onClick={detail.data ? () => setKpiModal("sentiment") : undefined}
            />
            <Kpi label="대표 근거 문장" value={kpi?.evidence ?? "-"} unit="개" onClick={detail.data ? () => setKpiModal("evidence") : undefined} />
            <Kpi label="분석 신뢰도" value={kpi?.confidence ?? "-"} unit="/100" onClick={detail.data ? () => setKpiModal("confidence") : undefined} />
          </div>

          <div className="card map-card">
            <div className="map-head">
              {companyId && company.data ? (
                <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
                  <CompanyAvatar name={company.data.name} stockCode={company.data.stockCode} industry={company.data.industries[0]?.name} size={44} />
                  <div>
                    <div className="kick" style={{ color: "var(--primary-2)" }}>
                      focused company
                    </div>
                    <div className="ttl">{company.data.name}</div>
                    <div className="meta">선택 뉴스 「{detail.data?.title.slice(0, 26) ?? "…"}…」 기준 관계망</div>
                  </div>
                </div>
              ) : (
                <div className="grow">
                  <div className="kick" style={{ color: "var(--primary-2)" }}>
                    selected news
                  </div>
                  <div className="ttl">{detail.data?.title ?? <Skeleton h={22} w={360} />}</div>
                  <div className="meta num mt-8">{detail.data ? [detail.data.publisher, fmtDateTime(detail.data.publishedAt), detail.data.author].filter(Boolean).join(" · ") : ""}</div>
                </div>
              )}
              <div className="row" style={{ gap: 8, flex: "none" }}>
                {companyId && company.data ? (
                  <>
                    <button
                      type="button"
                      className={`btn btn-g btn-sm ${company.data.watched ? "on" : ""}`}
                      onClick={() =>
                        authed
                          ? toggleWatch.mutate(
                              { companyId: company.data!.companyId, watched: company.data!.watched },
                              { onSuccess: (now) => toast(now ? "관심 기업으로 등록했습니다." : "관심 기업에서 해제했습니다.", "success") },
                            )
                          : openAuth("login")
                      }
                      style={{ color: company.data.watched ? "var(--primary-2)" : undefined }}
                    >
                      <Icon.Star filled={company.data.watched} /> {company.data.watched ? "관심 기업" : "관심 기업 +"}
                    </button>
                    <button type="button" className="btn btn-p btn-sm" onClick={() => navigate(`/?company=${companyId}`)}>
                      기업 상세 ↗
                    </button>
                  </>
                ) : (
                  detail.data && (
                    <>
                      <button
                        type="button"
                        className="btn btn-g btn-sm"
                        onClick={() => (authed ? toggleScrap.mutate({ newsId: detail.data!.newsId, scrapped: detail.data!.scrapped }, { onSuccess: (now) => toast(now ? "스크랩했습니다." : "스크랩을 해제했습니다.", "success") }) : openAuth("login"))}
                        style={{ color: detail.data.scrapped ? "var(--primary-2)" : undefined }}
                      >
                        <Icon.Bookmark filled={detail.data.scrapped} /> {detail.data.scrapped ? "스크랩됨" : "스크랩"}
                      </button>
                      <button type="button" className="btn btn-g btn-sm" onClick={() => setShowDetail(true)}>
                        상세 정보 ↗
                      </button>
                    </>
                  )
                )}
              </div>
            </div>
            {/* <div className="map-tools">
              <span className="pill">
                RELATION MAP · {mapNodes.length} NODES · {mapEdges.length} EDGES
              </span>
              <TypeLegend />
            </div> */}
            <div className="map-stage">
              {mapNodes.length ? (
                <RelationMap2D nodes={mapNodes} edges={mapEdges} related={detail.data?.relatedCompanies} highlightId={companyId} onPickCompany={(id) => setCompany(id)} />
              ) : (
                <div className="stage-loading">
                  <span className="kick">relation map</span>
                  <i />
                  <span>{detail.isLoading || universe.isLoading ? "관계 지도를 만드는 중" : "이 뉴스와 연결된 기업이 그래프에 없습니다"}</span>
                </div>
              )}
            </div>
          </div>

          {/* ---------------- 하단 분석 ---------------- */}
          {/* 두 카드(랭킹·경로)는 각자 전체 폭을 쓰던 것을 데스크톱에서는 나란히 배치하도록 바꿨다 —
              랭킹 목록의 점수 막대가 900px 가까이 늘어나 가늘고 길게 비어 보이던 문제가 있었다.
              1100px 이하에서는 기존처럼 한 칸(1fr)으로 접혀 세로로 쌓인다. */}
          <div className="analysis">
            <div className="card">
              <div className="kick">영향 분석</div>
              <div className="row between mt-8">
                <h2 className="sect">영향도가 큰 기업</h2>
                <span className="pill">점수순 · 100 기준</span>
              </div>
              <div className="mt-12">
                {!analysis && [0, 1, 2].map((i) => <Skeleton key={i} h={52} style={{ marginTop: 10 }} />)}
                {analysis?.ranking.map((r, i) => (
                  <button key={r.id} type="button" className="rank" onClick={() => setCompany(r.id)} title="이 기업 뉴스 보기">
                    <span className="rank-row">
                      <span className="kick num idx">{String(i + 1).padStart(2, "0")}</span>
                      <CompanyAvatar name={r.name} stockCode={r.stockCode} industry={r.industry} size={32} radius={16} />
                      <span className="nm">
                        <b>{r.name}</b>
                        <span className="meta num">
                          {[marketLabel(r.market), r.stockCode].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                      <ScoreBar score={r.score} color={r.hop === 0 ? "#829CFF" : "#F5A623"} />
                      <span className="sc">
                        <b className="num">{r.score}</b>
                        <span className="meta num">{r.hop === 0 ? "직접 언급" : `${r.hop} HOP`}</span>
                      </span>
                    </span>
                    {/* 왜 이 기업까지 영향이 이어지는지 — 예전엔 별도 "주요 경로" 카드에서 1개만
                        설명했는데, 랭킹에 있는 간접 전이(hop>0) 기업 전부에 대해 바로 아래에서
                        보여주는 걸로 바꿨다. 새 계산 없이 이미 갖고 있던 via/from 정보만 쓴다. */}
                    {r.hop > 0 && r.via && r.fromName && (
                      <span className="rank-why">
                        {r.name}
                        {josa(r.name, "은", "는")} {r.fromName}
                        {josa(r.fromName, "과", "와")} '{relationshipMeta(r.via.relationshipType).label}' 관계(점수 {Math.round(r.via.score)})로 이어져 있습니다.
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <div className="row mt-12" style={{ gap: 16 }}>
                <span className="meta row" style={{ gap: 6 }}>
                  <i className="dot" style={{ background: "#829CFF" }} /> 직접 언급 (기사 관련도 × 영향도)
                </span>
                <span className="meta row" style={{ gap: 6 }}>
                  <i className="dot" style={{ background: "#F5A623" }} /> 간접 전이 (관계 점수로 감쇠)
                </span>
              </div>
              <div className="meta mt-8" style={{ lineHeight: 1.7 }}>
                기사 영향도에 관계 점수(0~1)를 곱해 계산한 프로토타입 점수이며, 투자 판단 자료가 아닙니다.
              </div>
            </div>

            {/*
              예전 "주요 경로" 카드는 2홉 이상일 때만 의미가 있었는데 실제로는 거의 항상 1홉이라
              자리가 비다시피 했다. 그 "왜"는 위 랭킹 카드로 옮기고, 이 자리는 뉴스가 언급한
              기업들의 산업·관계 유형 분포로 바꿔서 기사 길이와 무관하게 항상 채워지게 했다.
            */}
            <div className="card">
              <div className="kick">기업·관계 개요</div>
              <div className="row between mt-8">
                <h2 className="sect" style={{ fontSize: 20 }}>
                  산업·관계 유형 분포
                </h2>
                <span className="pill">언급 기업 {mapNodes.length}개</span>
              </div>
              <div className="dist-row mt-12">
                <div className="dist-col">
                  <div className="lab">산업 분포</div>
                  {industryDist.length ? (
                    <div className="dist-chips">
                      {industryDist.map((d) => (
                        <span key={d.name} className="dist-chip">
                          {d.name} <b className="num">{d.count}</b>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="meta mt-8">표시할 산업 정보가 없습니다.</div>
                  )}
                </div>
                <div className="dist-col">
                  <div className="lab">관계 유형 분포</div>
                  {relDist.length ? (
                    <div className="dist-chips">
                      {relDist.map((d) => (
                        <span key={d.type} className="dist-chip">
                          <i className="dot" style={{ background: d.meta.color }} /> {d.meta.label} <b className="num">{d.count}</b>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="meta mt-8">언급된 기업들 사이의 직접적인 관계가 없습니다.</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <NewsDetailDialog open={showDetail} news={detail.data} activeCompanyId={companyId} onClose={() => setShowDetail(false)} onPickCompany={setCompany} />
      <KpiEvidenceDialog kind={kpiModal} news={detail.data} onClose={() => setKpiModal(null)} onPickCompany={setCompany} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 분석: 영향도 랭킹 + 최강 전이 경로 (클라이언트 계산)                        */
/* ------------------------------------------------------------------ */
interface RankRow {
  id: string;
  name: string;
  stockCode: string;
  market: string;
  industry: string;
  score: number;
  hop: number;
  /** hop>0(간접 전이) 행에서 "왜 이 기업까지 이어지는지" 표시에 쓴다 */
  via: GraphEdge | null;
  fromName: string | null;
}
const DECAY = 0.82;
interface Best {
  score: number;
  hop: number;
  from: string | null;
  via: GraphEdge | null;
}

function analyze(detail: NewsDetail, graph: LatestGraph) {
  const info = new Map(graph.nodes.map((n) => [n.companyId, n]));
  const adj = new Map<string, GraphEdge[]>();
  graph.edges.forEach((e) => {
    for (const id of [e.sourceCompanyId, e.targetCompanyId]) {
      if (!adj.has(id)) adj.set(id, []);
      adj.get(id)!.push(e);
    }
  });
  const best = new Map<string, Best>();
  detail.relatedCompanies.forEach((r) => {
    const s = Math.round((r.relevanceScore ?? 0) * (r.impactScore ?? 0) * 100);
    const cur = best.get(r.companyId);
    if (!cur || cur.score < s) best.set(r.companyId, { score: s, hop: 0, from: null, via: null });
  });
  // 1~2홉 전이
  const frontier = [...best.keys()];
  for (let hop = 1; hop <= 2; hop += 1) {
    const next: string[] = [];
    for (const id of frontier) {
      const b = best.get(id)!;
      if (b.hop !== hop - 1) continue;
      for (const e of adj.get(id) ?? []) {
        const o = e.sourceCompanyId === id ? e.targetCompanyId : e.sourceCompanyId;
        const s = Math.round(b.score * (e.score / 100) * DECAY);
        if (s < 3) continue;
        const cur = best.get(o);
        if (!cur || cur.score < s) {
          best.set(o, { score: s, hop, from: id, via: e });
          next.push(o);
        }
      }
    }
    frontier.push(...next);
  }
  const nameOf = (id: string) => info.get(id)?.name ?? detail.relatedCompanies.find((r) => r.companyId === id)?.name ?? id;
  const ranking: RankRow[] = [...best.entries()]
    .map(([id, b]) => {
      const n = info.get(id);
      const rel = detail.relatedCompanies.find((r) => r.companyId === id);
      return {
        id,
        name: n?.name ?? rel?.name ?? id,
        stockCode: n?.stockCode ?? "",
        market: n?.market ?? "",
        industry: n?.industryName ?? "",
        score: b.score,
        hop: b.hop,
        via: b.via,
        fromName: b.from ? nameOf(b.from) : null,
      };
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, 6);

  return { ranking };
}

export { relationshipMeta };
