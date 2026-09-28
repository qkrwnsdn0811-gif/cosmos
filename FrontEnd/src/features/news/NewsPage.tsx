import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import type { GraphEdge, LatestGraph, LatestGraphNode, NewsDetail, NewsRelatedCompany, RelationshipType, Sentiment } from "@/api/types";
import { cleanNewsSummary, fmtDateTime, fmtRelative, josa, sortNewsByLatest } from "@/lib/format";
import { SENTIMENTS, SENTIMENT_META, UNCLASSIFIED_INDUSTRY, marketLabel, relationshipMeta } from "@/lib/meta";
import { useCompany, useIndustries, useLatestGraph, useNewsDetail, useNewsFeed, useToggleScrap } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { CompanyAvatar, Empty, ErrorNotice, Icon, Kpi, ScoreBar, SentimentBadge, Skeleton } from "@/components/ui";
import DistributionDialog from "./DistributionDialog";
import KpiEvidenceDialog, { type KpiEvidenceKind } from "./KpiEvidenceDialog";
import RelationMap2D from "./RelationMap2D";
import "./news.css";

/**
 * 뉴스 (화면구성도 ⑦~⑨, 시안 A1). 좌측 컨텍스트 레일 + 우측 관계도.
 * 관계도의 기업 노드를 누르면 좌측 목록이 그 기업 뉴스로 전환되고 브레드크럼이 복귀 경로를 보여준다.
 */
export default function NewsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useUi((s) => s.toast);
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);

  const companyId = params.get("company");
  const [industryId, setIndustryId] = useState<string | null>(null);
  const [sentiment, setSentiment] = useState<Sentiment | null>(null);
  // ?news= 로 URL에 동기화한다 — 은하 페이지의 관계선 보기로 이탈했다가 돌아왔을 때 같은 기사가 다시 열리도록
  const [selectedId, setSelectedId] = useState<string | null>(() => params.get("news"));
  /* 목록에서 뉴스를 고르면 — 1100px 이하(목록 아래에 분석이 세로로 쌓이는 레이아웃)에서는 선택 뉴스 카드(.map-card)로 바로 내려간다.
     넓은 화면은 두 컬럼이 나란히 보여 스크롤할 것이 없다. 스크롤 여백은 news.css 의 scroll-margin-top(헤더 높이) */
  const pickNews = (id: string) => {
    setSelectedId(id);
    const next = new URLSearchParams(params);
    next.set("news", id);
    setParams(next, { replace: true });
    if (!window.matchMedia("(max-width: 1100px)").matches) return;
    // .map-card 는 늘 그려져 있어 렌더를 기다릴 필요가 없다 (rAF 에 미루면 숨겨진 탭에서는 실행되지 않는다)
    document.querySelector(".map-card")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [kpiModal, setKpiModal] = useState<KpiEvidenceKind | null>(null);
  const [showDist, setShowDist] = useState(false);

  // 입력마다 요청이 나가지 않도록 디바운스한다 (live 모드에서 백엔드 호출 폭주 방지)
  useEffect(() => {
    const t = setTimeout(() => setKeyword(keywordInput.trim()), 300);
    return () => clearTimeout(t);
  }, [keywordInput]);

  const industries = useIndustries();
  const universe = useLatestGraph(null);
  const company = useCompany(companyId);
  const feed = useNewsFeed({ companyId: companyId ?? undefined, industryId: companyId ? undefined : industryId ?? undefined, sentiment: sentiment ?? undefined, keyword: keyword || undefined, size: 20 });
  // API가 준 순서를 신뢰하지 않고 발행일 내림차순(최신 먼저)으로 다시 정렬한다
  const items = useMemo(() => sortNewsByLatest(feed.data?.pages.flatMap((p) => p.items) ?? []), [feed.data]);

  // 목록이 바뀌거나(필터 변경 등) 선택된 기사가 새 목록에 없으면 첫 기사를 기본값으로 본다.
  // effect에서 setState로 동기화하면 렌더가 한 번 더 캐스케이드되므로, 렌더링 중 파생값으로 계산한다.
  const activeId = selectedId && items.some((n) => n.newsId === selectedId) ? selectedId : items[0]?.newsId ?? null;

  const detail = useNewsDetail(activeId);
  const toggleScrap = useToggleScrap();

  const setCompany = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("company", id);
    else next.delete("company");
    // 기업 문맥이 바뀌면 이전에 고른 기사는 새 목록에 없을 수 있다 — 선택도 함께 비운다
    next.delete("news");
    setParams(next);
    setSentiment(null);
    setSelectedId(null);
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
   * 산업·관계 유형 분포 — 새 API 호출 없이 관계 지도에 이미 쓰던 mapNodes/mapEdges만으로 계산한다.
   * 예전엔 랭킹 카드 옆에 항상 펼쳐 둔 카드였지만, 언급 기업이 몇 개 안 되는 뉴스가 대부분이라
   * 칩 한두 줄만 채워지고 카드 높이(랭킹 카드 기준)만큼 빈 공간이 크게 남는 문제가 있었다.
   * 항상 봐야 할 만큼 핵심적인 정보는 아니라고 보고 스크랩·원문 보기 버튼 옆의 보조 버튼 +
   * 모달(DistributionDialog)로 옮기고, 랭킹 카드가 하단 영역 전체를 쓰도록 바꿨다.
   */
  const industryDist = useMemo(() => {
    const m = new Map<string, number>();
    mapNodes.forEach((n) => {
      const key = n.industryName ?? UNCLASSIFIED_INDUSTRY;
      m.set(key, (m.get(key) ?? 0) + 1);
    });
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count }));
  }, [mapNodes]);
  const relDist = useMemo(() => {
    const m = new Map<RelationshipType, number>();
    mapEdges.forEach((e) => m.set(e.relationshipType, (m.get(e.relationshipType) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([type, count]) => ({ type, count, meta: relationshipMeta(type) }));
  }, [mapEdges]);

  return (
    <div className="page news-viewport">
      <div className="row between wrap" style={{ marginBottom: 14 }}>
        <span className="status-line">
          <i className="dot" />
          {universe.data ? `스냅샷 ${fmtDateTime(universe.data.asOfAt)} 기준 · 1시간 단위 갱신` : universe.isError ? "공개된 관계 스냅샷 없음 — 관계 지도는 분석 결과 게시 후 나타납니다" : "스냅샷 확인 중"}
        </span>
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
                {company.data && <CompanyAvatar name={company.data.name} stockCode={company.data.stockCode} market={company.data.market} industry={company.data.industries[0]?.name} size={40} />}
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
                    최신 뉴스
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
            {!feed.isLoading && feed.isError && !items.length && <ErrorNotice message="뉴스를 불러오지 못했습니다." onRetry={() => feed.refetch()} />}
            {!feed.isLoading && !feed.isError && !items.length && <Empty>조건에 맞는 뉴스가 없습니다.</Empty>}
            {items.map((n) => (
              <article key={n.newsId} className={`news-card ${n.newsId === activeId ? "on" : ""}`} onClick={() => pickNews(n.newsId)} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && pickNews(n.newsId)}>
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
                {cleanNewsSummary(n.summary) && <div className="summary clamp-2">{cleanNewsSummary(n.summary)}</div>}
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
        <section className="news-analysis-col" aria-label="관계 분석">
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
                  <CompanyAvatar name={company.data.name} stockCode={company.data.stockCode} market={company.data.market} industry={company.data.industries[0]?.name} size={44} />
                  <div>
                    <div className="kick" style={{ color: "var(--primary-2)" }}>
                      focused company
                    </div>
                    <div className="row" style={{ gap: 8, alignItems: "baseline" }}>
                      <div className="ttl">{company.data.name}</div>
                      {/* 기업 상세 이동은 스크랩/산업·관계 분포/원문 보기와 성격이 달라(뉴스가 아니라 기업 자체로 이동)
                          공용 버튼 줄에서 빼고, 어떤 기업이 focused 됐는지 보여주는 이름 바로 옆에 둔다 */}
                      <button type="button" className="btn btn-g btn-xs" onClick={() => navigate(`/?company=${companyId}`)}>
                        기업 상세 ↗
                      </button>
                    </div>
                    <div className="meta">선택 뉴스 「{detail.isError ? "불러오기 실패" : detail.data?.title.slice(0, 26) ?? "…"}…」 기준 관계망</div>
                  </div>
                </div>
              ) : (
                <div className="grow">
                  <div className="kick" style={{ color: "var(--primary-2)" }}>
                    selected news
                  </div>
                  {detail.isError ? (
                    <ErrorNotice message="선택한 뉴스를 불러오지 못했습니다." onRetry={() => detail.refetch()} />
                  ) : (
                    <>
                      <div className="ttl">{detail.data?.title ?? <Skeleton h={22} w={360} />}</div>
                      <div className="meta num mt-8">{detail.data ? [detail.data.publisher, fmtDateTime(detail.data.publishedAt), detail.data.author].filter(Boolean).join(" · ") : ""}</div>
                      {cleanNewsSummary(detail.data?.summary) && <p className="map-summary">{cleanNewsSummary(detail.data?.summary)}</p>}
                    </>
                  )}
                </div>
              )}
              {/* 기업으로 필터링해서 볼 때도(관계도의 기업 노드·랭킹 클릭 등) 이 버튼 줄은 항상 "지금 보고 있는
                  뉴스" 기준으로 스크랩/산업·관계 분포/원문 보기를 일관되게 제공한다 — 기업 자체로 이동하는
                  기업 상세는 성격이 달라 위 focused company 이름 옆으로 옮겼다(companyId 분기 없음) */}
              <div className="row" style={{ gap: 8, flex: "none" }}>
                {detail.data && (
                  <>
                    <button
                      type="button"
                      className="btn btn-g btn-sm"
                      onClick={() => (authed ? toggleScrap.mutate({ newsId: detail.data!.newsId, scrapped: detail.data!.scrapped }, { onSuccess: (now) => toast(now ? "스크랩했습니다." : "스크랩을 해제했습니다.", "success") }) : openAuth("login"))}
                      style={{ color: detail.data.scrapped ? "var(--primary-2)" : undefined }}
                    >
                      <Icon.Bookmark filled={detail.data.scrapped} /> {detail.data.scrapped ? "스크랩됨" : "스크랩"}
                    </button>
                    <button type="button" className="btn btn-g btn-sm" onClick={() => setShowDist(true)}>
                      산업·관계 분포
                    </button>
                    <a className="btn btn-p btn-sm" href={detail.data.originalUrl} target="_blank" rel="noreferrer" style={{ flex: "none" }}>
                      원문 보기 <Icon.External />
                    </a>
                  </>
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
                <RelationMap2D
                  nodes={mapNodes}
                  edges={mapEdges}
                  related={detail.data?.relatedCompanies}
                  highlightId={companyId}
                  onPickCompany={(id) => setCompany(id)}
                  onOpenInGalaxy={(relationshipId) =>
                    // 은하 쪽 관계 화면에서 "뉴스로 돌아가기"를 누르면 지금 보고 있던 이 위치(선택 기사·기업 문맥)로 돌아올 수 있도록
                    // 현재 경로를 함께 넘긴다 (GalaxyPage.tsx newsReturnTo)
                    navigate(`/?edge=${relationshipId}`, { state: { newsReturnTo: `${location.pathname}${location.search}` } })
                  }
                />
              ) : (
                <div className="stage-loading">
                  <span className="kick">relation map</span>
                  <i />
                  <span>
                    {detail.isLoading || universe.isLoading
                      ? "관계 지도를 만드는 중"
                      : universe.isError
                        ? "공개된 관계 스냅샷이 없어 관계 지도를 표시할 수 없습니다"
                        : "이 뉴스와 연결된 기업이 그래프에 없습니다"}
                  </span>
                </div>
              )}
            </div>
          </div>

          {/* ---------------- 하단 분석 ---------------- */}
          {/* "산업·관계 유형 분포" 카드를 스크랩·원문 보기 옆 모달로 옮기면서 이 영역을 랭킹
              카드 하나가 전부 쓰도록 바꿨다. 랭킹 목록은 한 줄에 기업 1개씩(.rank-list) 나열하되,
              예전엔 이름 아래 두 번째 줄로 내려가던 시장·종목코드와, 행 아래 별도 줄이던 "왜
              이어지는지" 설명을 모두 이름 오른쪽(같은 줄)으로 옮겨 항목의 상하 길이를 줄였다.
              점수 막대는 한 줄 전체 폭으로 늘어지지 않도록 고정 폭 래퍼(.rank-bar-wrap)로 감쌌다. */}
          <div className="analysis">
            <div className="card">
              <div className="kick">영향 분석</div>
              <div className="row between mt-8">
                <h2 className="sect">영향도가 큰 기업</h2>
                <span className="pill">점수순 · 100 기준</span>
              </div>
              <div className="rank-list">
                {!analysis && [0, 1, 2, 3].map((i) => <Skeleton key={i} h={44} style={{ marginTop: i ? 8 : 0 }} />)}
                {analysis?.ranking.map((r, i) => {
                  // 왜 이 기업까지 영향이 이어지는지 — 간접 전이(hop>0) 행에서만 있다. 예전엔 행
                  // 아래 별도 줄로 보여줬지만, 항목 높이를 줄이기 위해 이름 옆 meta 텍스트에
                  // 이어 붙였다(폭이 모자라면 말줄임). 문장 속 기업명 둘·관계 유형·점수(AA는 BB와
                  // CC 관계(점수 DD)로 이어져 있습니다)가 눈에 띄도록 각각 다른 색을 준다 — AA는 이
                  // 행 자체의 간접 전이 색(주황, 막대 색과 동일), BB는 직접 언급 색(파랑)으로 둘을
                  // 구분하고, CC는 관계 유형이 다른 화면에서도 쓰는 고유색(relationshipMeta), DD는
                  // 점수를 가리키는 별도 강조색(초록)을 쓴다.
                  const why =
                    r.hop > 0 && r.via && r.fromName
                      ? {
                          name: r.name,
                          from: r.fromName,
                          relLabel: relationshipMeta(r.via.relationshipType).label,
                          relColor: relationshipMeta(r.via.relationshipType).color,
                          score: Math.round(r.via.score),
                        }
                      : null;
                  const whyPlain = why ? `${why.name}${josa(why.name, "은", "는")} ${why.from}${josa(why.from, "과", "와")} '${why.relLabel}' 관계(점수 ${why.score})로 이어져 있습니다.` : null;
                  return (
                    <button key={r.id} type="button" className="rank" onClick={() => setCompany(r.id)} title={whyPlain ?? "이 기업 뉴스 보기"}>
                      <span className="rank-row">
                        <span className="kick num idx">{String(i + 1).padStart(2, "0")}</span>
                        <CompanyAvatar name={r.name} companyId={r.id} stockCode={r.stockCode} market={r.market} industry={r.industry} size={32} radius={16} />
                        <span className="nm">
                          <b>{r.name}</b>
                          <span className="meta num">
                            {[marketLabel(r.market), r.stockCode].filter(Boolean).join(" · ")}
                            {why && (
                              <>
                                {" · "}
                                <span className="why-hl" style={{ color: "#F5A623" }}>
                                  {why.name}
                                </span>
                                {josa(why.name, "은", "는")}{" "}
                                <span className="why-hl" style={{ color: "#829CFF" }}>
                                  {why.from}
                                </span>
                                {josa(why.from, "과", "와")} '
                                <span className="why-hl" style={{ color: why.relColor }}>
                                  {why.relLabel}
                                </span>
                                ' 관계(점수{" "}
                                <span className="why-hl" style={{ color: "#2AC769" }}>
                                  {why.score}
                                </span>
                                )로 이어져 있습니다.
                              </>
                            )}
                          </span>
                        </span>
                        <span className="rank-bar-wrap">
                          <ScoreBar score={r.score} color={r.hop === 0 ? "#829CFF" : "#F5A623"} />
                        </span>
                        <span className="sc">
                          <b className="num">{r.score}</b>
                          <span className="meta num">{r.hop === 0 ? "직접 언급" : `${r.hop} HOP`}</span>
                        </span>
                      </span>
                    </button>
                  );
                })}
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
          </div>
        </section>
      </div>

      <KpiEvidenceDialog kind={kpiModal} news={detail.data} onClose={() => setKpiModal(null)} onPickCompany={setCompany} />
      <DistributionDialog open={showDist} companyCount={mapNodes.length} industryDist={industryDist} relDist={relDist} onClose={() => setShowDist(false)} />
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
    // 예전엔 세로 한 줄 목록이라 6개로 제한했다. 이제 카드형 그리드로 넓어진 하단 영역을 쓰므로
    // (기업이 많은 뉴스에서는 여러 열로 자연히 채워진다) 9개까지 보여준다.
    .slice(0, 9);

  return { ranking };
}

export { relationshipMeta };
