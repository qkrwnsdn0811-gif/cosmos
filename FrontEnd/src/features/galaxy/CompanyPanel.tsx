import { useMemo, useState } from "react";
import type { CompanyComment, MetricWindow, PricePeriod, Sentiment } from "@/api/types";
import { API_MODE } from "@/api";
import { CompanyAvatar, Empty, Icon, IndustryBadge, SentimentBadge, Skeleton } from "@/components/ui";
import PriceChart, { Sparkbars } from "@/components/PriceChart";
import { fmtCompact, fmtCompactPrice, fmtDateTime, fmtNumber, fmtPct, fmtPctFrom01, fmtPrice, fmtRelative, fmtTradingDate } from "@/lib/format";
import type { SceneModel } from "@/lib/graph";
import { PREVIEW_UI } from "@/lib/guide";
import { SENTIMENTS, SENTIMENT_META, marketCurrency } from "@/lib/meta";
import { ROLE_META, roleColor, roleCounts } from "@/lib/roles";
import {
  useCompany,
  useCompanyComments,
  useCompanyMetrics,
  useDeleteComment,
  useEditComment,
  useMetricsHistory,
  useNewsFeed,
  useStockPrices,
  useToggleScrap,
  useToggleWatch,
  useWriteComment,
} from "@/lib/queries";
import { useGalaxy, type PanelTab } from "@/store/galaxy";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";

interface Props {
  companyId: string;
  /** 미리보기 문맥(은하 뷰) — 있으면 이 모델에서 1홉·2홉 연결 단계를 보여 준다 */
  model?: SceneModel | null;
  /** "관계망 보기" — 그 기업 중심으로 워프. 없으면 버튼을 그리지 않는다(이미 그 기업 안에 있을 때) */
  onWarp?: () => void;
  onClose: () => void;
  onPickCompany: (id: string) => void;
}

const TABS: { key: PanelTab; label: string }[] = [
  { key: "news", label: "뉴스" },
  { key: "price", label: "주가" },
  { key: "board", label: "게시판" },
];

/** 기업 인텔리전스 패널 — 뉴스 · 주가 · 게시판 (화면구성도 ②~⑥) */
export default function CompanyPanel({ companyId, model = null, onWarp, onClose, onPickCompany }: Props) {
  const company = useCompany(companyId);
  const tab = useGalaxy((s) => s.panelTab);
  const setTab = useGalaxy((s) => s.setPanelTab);
  const window = useGalaxy((s) => s.window);
  const metrics = useCompanyMetrics(companyId, window);
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const toggleWatch = useToggleWatch();
  const c = company.data;

  return (
    <aside className="card panel" aria-label="기업 인텔리전스">
      <div className="panel-head">
        <span className="kick">기업 인텔리전스</span>
        <div className="row" style={{ gap: 6 }}>
          {onWarp && (
            <button type="button" className="btn btn-p btn-xs" onClick={onWarp} title={PREVIEW_UI.warpTitle}>
              {PREVIEW_UI.warp}
            </button>
          )}
          <button type="button" className="icon-btn" onClick={onClose} aria-label="패널 닫기">
            <Icon.Close />
          </button>
        </div>
      </div>
      {model && model.nodeById.has(companyId) && <ConnectionSteps model={model} companyId={companyId} onPick={onPickCompany} />}
      <div className="panel-id">
        {c ? <CompanyAvatar name={c.name} stockCode={c.stockCode} industry={c.industries.find((i) => i.primary)?.name} size={56} /> : <Skeleton h={56} w={56} style={{ borderRadius: 14 }} />}
        <div className="grow">
          {c ? (
            <>
              <div className="name">{c.name}</div>
              <div className="meta num sub">{[c.stockCode, c.market, c.nameEn].filter(Boolean).join(" · ")}</div>
            </>
          ) : (
            <>
              <Skeleton h={22} w={140} />
              <Skeleton h={12} w={180} style={{ marginTop: 8 }} />
            </>
          )}
        </div>
        {c && (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
            <IndustryBadge name={c.industries.find((i) => i.primary)?.name ?? "-"} />
            <button
              type="button"
              className={`chip chip-sm ${c.watched ? "on" : ""}`}
              disabled={toggleWatch.isPending}
              onClick={() => {
                if (!authed) return openAuth("login");
                toggleWatch.mutate(
                  { companyId, watched: c.watched },
                  { onSuccess: (now) => toast(now ? "관심 기업에 추가했습니다." : "관심 기업에서 해제했습니다.", "success"), onError: () => toast("처리하지 못했습니다.", "error") },
                );
              }}
              aria-pressed={c.watched}
            >
              <Icon.Star filled={c.watched} />
              {c.watched ? "관심 기업" : "관심 등록"}
            </button>
          </div>
        )}
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === "news" && metrics.data?.newsMentionCount != null && <b className="num">{metrics.data.newsMentionCount}</b>}
          </button>
        ))}
      </div>
      <div className="panel-body scroll">
        {tab === "news" && <NewsTab companyId={companyId} onPickCompany={onPickCompany} />}
        {tab === "price" && c && <PriceTab companyId={companyId} market={c.market} marketCap={c.marketCap ?? null} listedShares={c.listedShares ?? null} />}
        {tab === "board" && c && <BoardTab companyId={companyId} companyName={c.name} />}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------ */
/* 뉴스 탭                                                              */
/* ------------------------------------------------------------------ */
function NewsTab({ companyId, onPickCompany }: { companyId: string; onPickCompany: (id: string) => void }) {
  const [sentiment, setSentiment] = useState<Sentiment | null>(null);
  const feed = useNewsFeed({ companyId, sentiment: sentiment ?? undefined, size: 10 });
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const toggleScrap = useToggleScrap();
  const items = feed.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <div className="row wrap" style={{ gap: 8 }}>
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
      <div className="stack mt-16">
        {feed.isLoading && [0, 1, 2].map((i) => <Skeleton key={i} h={104} style={{ borderRadius: 14 }} />)}
        {!feed.isLoading && !items.length && <Empty>해당 조건의 뉴스가 없습니다.</Empty>}
        {items.map((n) => (
          <article key={n.newsId} className="news-card">
            <div className="row between">
              <div className="row" style={{ gap: 8 }}>
                <SentimentBadge value={n.sentiment} />
                <span className="meta num">{[n.publisher, fmtRelative(n.publishedAt)].filter(Boolean).join(" · ")}</span>
              </div>
              <button
                type="button"
                className="btn btn-g btn-xs"
                aria-label={n.scrapped ? "스크랩 해제" : "스크랩"}
                style={{ color: n.scrapped ? "var(--primary-2)" : undefined }}
                onClick={() => (authed ? toggleScrap.mutate({ newsId: n.newsId, scrapped: n.scrapped }) : openAuth("login"))}
              >
                <Icon.Bookmark filled={n.scrapped} />
              </button>
            </div>
            <div className="title">{n.title}</div>
            <div className="summary clamp-2">{n.summary}</div>
            <div className="foot">
              <div className="co">
                {n.relatedCompanies
                  .filter((r) => r.companyId !== companyId)
                  .slice(0, 3)
                  .map((r) => (
                    <button key={r.companyId} type="button" onClick={() => onPickCompany(r.companyId)} title={`${r.name} 중심으로 이동`}>
                      {r.name}
                    </button>
                  ))}
              </div>
              <a href={n.originalUrl} target="_blank" rel="noreferrer" className="link-btn row" style={{ gap: 4 }}>
                원문 보기 <Icon.External />
              </a>
            </div>
          </article>
        ))}
      </div>
      {feed.hasNextPage && (
        <button type="button" className="more-btn" onClick={() => feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
          {feed.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
        </button>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 주가 탭                                                              */
/* ------------------------------------------------------------------ */
const PERIODS: PricePeriod[] = ["1M", "3M", "6M", "1Y"];
const WINDOWS: MetricWindow[] = ["7D", "30D", "90D"];

function PriceTab({ companyId, market, marketCap, listedShares }: { companyId: string; market: string; marketCap: number | null; listedShares: number | null }) {
  const period = useGalaxy((s) => s.pricePeriod);
  const setPeriod = useGalaxy((s) => s.setPricePeriod);
  const window = useGalaxy((s) => s.window);
  const setWindow = useGalaxy((s) => s.setWindow);
  const prices = useStockPrices(companyId, period);
  const year = useStockPrices(companyId, "1Y");
  const metrics = useCompanyMetrics(companyId, window);
  const history = useMetricsHistory(companyId, { window });
  const currency = marketCurrency(market);
  const items = prices.data?.items ?? [];
  const last = items.at(-1);
  const first = items[0];
  const change = last && first ? ((last.closePrice - first.closePrice) / first.closePrice) * 100 : null;
  const hi52 = year.data ? Math.max(...year.data.items.map((c) => c.highPrice)) : null;
  const lo52 = year.data ? Math.min(...year.data.items.map((c) => c.lowPrice)) : null;
  const avgVol = items.length ? items.reduce((s, c) => s + c.tradingVolume, 0) / items.length : null;
  // 시가총액이 없는 동안 기간의 체감 규모를 보여 주는 파생값 (종가 × 거래량 평균)
  const avgTurnover = items.length ? items.reduce((s, c) => s + c.closePrice * c.tradingVolume, 0) / items.length : null;
  const mentionSeries = useMemo(() => history.data?.items.map((p) => p.newsMentionCount) ?? [], [history.data]);
  const periodLabel = { "1M": "최근 1개월", "3M": "최근 3개월", "6M": "최근 6개월", "1Y": "최근 1년" }[period];

  return (
    <>
      <div className="seg" role="tablist" aria-label="기간">
        {PERIODS.map((p) => (
          <button key={p} type="button" className={period === p ? "on" : ""} onClick={() => setPeriod(p)}>
            {p}
          </button>
        ))}
      </div>
      <div className="price-head">
        <div>
          <div className="lab">{periodLabel}</div>
          <div className="v num">{last ? fmtPrice(last.closePrice, currency) : <Skeleton h={30} w={140} />}</div>
        </div>
        {change !== null && (
          <div className="num" style={{ fontSize: 15, fontWeight: 700, color: change >= 0 ? "#4FE8C0" : "#FF8FA3" }}>
            {fmtPct(change)}
          </div>
        )}
      </div>
      <div className="mt-12">{items.length ? <PriceChart data={items} color="auto" /> : <Skeleton h={150} />}</div>
      <div className="stat-grid mt-16">
        {/* 시가총액·상장주식수는 백엔드가 값을 줄 때만 나타난다 (company 테이블에 아직 없는 컬럼) */}
        {marketCap != null && (
          <div className="stat">
            <div className="lab">시가총액</div>
            <div className="v num">{fmtCompactPrice(marketCap, currency)}</div>
            {listedShares != null && <div className="meta num mt-8">상장주식수 {fmtCompact(listedShares)}주</div>}
          </div>
        )}
        <div className="stat">
          <div className="lab">평균 거래량</div>
          <div className="v num">{fmtCompact(avgVol)}</div>
        </div>
        <div className="stat">
          <div className="lab">평균 거래대금</div>
          <div className="v num">{fmtCompactPrice(avgTurnover, currency)}</div>
        </div>
        <div className="stat">
          <div className="lab">마지막 거래일</div>
          <div className="v num">{fmtTradingDate(last?.tradingAt)}</div>
        </div>
        <div className="stat">
          <div className="lab">52주 최고</div>
          <div className="v num">{fmtPrice(hi52, currency)}</div>
        </div>
        <div className="stat">
          <div className="lab">52주 최저</div>
          <div className="v num">{fmtPrice(lo52, currency)}</div>
        </div>
      </div>

      <div className="row between mt-20">
        <span className="kick">뉴스 · 관계 지표</span>
        <div className="seg" style={{ padding: 3 }}>
          {WINDOWS.map((w) => (
            <button key={w} type="button" className={window === w ? "on" : ""} style={{ padding: "4px 9px", fontSize: 12 }} onClick={() => setWindow(w)}>
              {w}
            </button>
          ))}
        </div>
      </div>
      <div className="stat-grid mt-12">
        <div className="stat">
          <div className="lab">뉴스 언급</div>
          <div className="v num">
            {fmtNumber(metrics.data?.newsMentionCount)} <span className="lab">건</span>
          </div>
          {mentionSeries.length > 0 && (
            <div className="mt-8">
              <Sparkbars values={mentionSeries} />
            </div>
          )}
        </div>
        <div className="stat">
          <div className="lab">뉴스 분위기</div>
          <div className="v num">
            {fmtPctFrom01(metrics.data?.sentimentScore, "")} <span className="lab">/100</span>
          </div>
          <div className="meta num mt-8">
            <span style={{ color: "var(--pos-fg)" }}>긍정 {fmtNumber(metrics.data?.positiveCount)}</span> · <span style={{ color: "var(--neg-fg)" }}>부정 {fmtNumber(metrics.data?.negativeCount)}</span>
          </div>
        </div>
        <div className="stat">
          <div className="lab">연결 관계</div>
          <div className="v num">
            {fmtNumber(metrics.data?.relationshipCount)} <span className="lab">개</span>
          </div>
        </div>
        <div className="stat">
          <div className="lab">측정 시각</div>
          <div className="v num" style={{ fontSize: 13 }}>
            {fmtDateTime(metrics.data?.measuredAt)}
          </div>
        </div>
      </div>
      {API_MODE === "mock" && <div className="meta mt-16">프로토타입 시계열 · 실제 시세 API 연동 전</div>}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* 게시판 탭 (기업 커뮤니티 댓글)                                          */
/* ------------------------------------------------------------------ */
/** 명세: 댓글 내용은 공백 제외 1~500자 */
const COMMENT_MAX = 500;

function BoardTab({ companyId, companyName }: { companyId: string; companyName: string }) {
  const comments = useCompanyComments(companyId);
  const user = useSession((s) => s.user);
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const write = useWriteComment(companyId);
  const [draft, setDraft] = useState("");
  const items = comments.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <div className="row between">
        <span className="kick">{companyName} 게시판</span>
        <span className="meta num">{items.length}개{comments.hasNextPage ? "+" : ""}</span>
      </div>
      <div className="field mt-12" style={{ alignItems: "flex-end", padding: "0 10px 8px 14px" }}>
        <textarea
          rows={2}
          value={draft}
          placeholder={user ? "근거를 함께 남겨주세요" : "로그인 후 의견을 남길 수 있습니다"}
          disabled={!user}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={() => !user && openAuth("login")}
          maxLength={COMMENT_MAX}
        />
        <span className="meta num" style={{ alignSelf: "flex-end", padding: "0 8px 6px" }}>
          {draft.length}/{COMMENT_MAX}
        </span>
        <button
          type="button"
          className="btn btn-p btn-xs"
          disabled={!user || !draft.trim() || write.isPending}
          onClick={() =>
            write.mutate(draft, {
              onSuccess: () => {
                setDraft("");
                toast("의견을 등록했습니다.", "success");
              },
              onError: () => toast("등록하지 못했습니다.", "error"),
            })
          }
        >
          등록
        </button>
      </div>
      <div className="stack mt-16">
        {comments.isLoading && [0, 1, 2].map((i) => <Skeleton key={i} h={84} style={{ borderRadius: 13 }} />)}
        {!comments.isLoading && !items.length && <Empty>첫 의견을 남겨보세요.</Empty>}
        {items.map((c) => (
          <CommentItem key={c.commentId} comment={c} mine={user?.userId === c.author.userId} />
        ))}
      </div>
      {comments.hasNextPage && (
        <button type="button" className="more-btn" onClick={() => comments.fetchNextPage()} disabled={comments.isFetchingNextPage}>
          더 보기
        </button>
      )}
      <div className="notice mt-16">투자 권유가 아닌 분석 근거를 공유해 주세요. 기사·공시 원문 출처를 함께 남겨주세요. 개인정보와 확인되지 않은 소문은 금지됩니다.</div>
    </>
  );
}

function CommentItem({ comment, mine }: { comment: CompanyComment; mine: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(comment.content);
  const edit = useEditComment();
  const remove = useDeleteComment();
  const toast = useUi((s) => s.toast);
  return (
    <div className="comment">
      <div className="who">
        <b>
          {comment.author.nickname}
          <span className="meta" style={{ marginLeft: 8 }}>
            {fmtRelative(comment.createdAt)}
            {comment.edited && " · 수정됨"}
          </span>
        </b>
        {mine && !editing && (
          <div className="acts">
            <button type="button" onClick={() => setEditing(true)}>
              수정
            </button>
            <button
              type="button"
              onClick={() => {
                if (!confirm("이 댓글을 삭제할까요?")) return;
                remove.mutate(comment.commentId, { onSuccess: () => toast("삭제했습니다."), onError: () => toast("삭제하지 못했습니다.", "error") });
              }}
            >
              삭제
            </button>
          </div>
        )}
      </div>
      {editing ? (
        <div className="mt-8">
          <div className="field" style={{ padding: "0 12px" }}>
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} maxLength={COMMENT_MAX} />
          </div>
          <div className="row mt-8" style={{ justifyContent: "flex-end", gap: 6 }}>
            <span className="meta num grow">
              {text.length}/{COMMENT_MAX}
            </span>
            <button type="button" className="btn btn-g btn-xs" onClick={() => (setEditing(false), setText(comment.content))}>
              취소
            </button>
            <button
              type="button"
              className="btn btn-p btn-xs"
              disabled={!text.trim() || edit.isPending}
              onClick={() => edit.mutate({ commentId: comment.commentId, content: text }, { onSuccess: () => setEditing(false), onError: () => toast("수정하지 못했습니다.", "error") })}
            >
              저장
            </button>
          </div>
        </div>
      ) : (
        <div className="body">{comment.content}</div>
      )}
    </div>
  );
}

/**
 * 연결 단계 — 미리보기 패널 맨 위. 1홉 이웃을 역할(공급사·고객·투자자…)별로 묶고 2홉까지 몇 기업이 닿는지 센다.
 * 이웃 이름을 누르면 그 기업이 미리보기로 바뀐다(워프 아님). 다른 행성에 커서를 올리면 이 기업에서 가는 경로가 상단 스트립에 뜬다
 */
function ConnectionSteps({ model, companyId, onPick }: { model: SceneModel; companyId: string; onPick: (id: string) => void }) {
  const minScore = useGalaxy((s) => s.minScore);
  const activeTypes = useGalaxy((s) => s.activeTypes);
  const { counts, hop1, hop2 } = useMemo(() => {
    const allowed = (e: { score: number; type: string }) => e.score >= minScore && activeTypes.has(e.type as never);
    const counts = roleCounts(model, companyId, allowed);
    const hop1 = new Set(counts.flatMap((c) => c.ids));
    const hop2 = new Set<string>();
    hop1.forEach((id) => model.neighbors.get(id)?.forEach((n2) => {
      if (n2 !== companyId && !hop1.has(n2)) hop2.add(n2);
    }));
    return { counts, hop1, hop2 };
  }, [model, companyId, minScore, activeTypes]);
  return (
    <section className="panel-steps">
      <div className="row between">
        <span className="kick">{PREVIEW_UI.steps}</span>
        <span className="meta num">{PREVIEW_UI.hops(hop1.size, hop2.size)}</span>
      </div>
      {counts.length === 0 ? (
        <div className="meta mt-8">{PREVIEW_UI.noLinks}</div>
      ) : (
        <div className="stack mt-8" style={{ gap: 6 }}>
          {counts.map((rc) => (
            <div key={rc.role} className="steps-role">
              <span className="steps-role-name">
                <i className="dot" style={{ background: roleColor(rc.role) }} />
                {ROLE_META[rc.role].label} <b className="num">{rc.count}</b>
              </span>
              <span className="steps-role-names">
                {rc.ids.map((id) => (
                  <button key={id} type="button" className="chip chip-sm" onClick={() => onPick(id)} title={PREVIEW_UI.pickTitle}>
                    {model.nodeById.get(id)?.name ?? id}
                  </button>
                ))}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="meta mt-8">{PREVIEW_UI.pathHint}</div>
    </section>
  );
}
