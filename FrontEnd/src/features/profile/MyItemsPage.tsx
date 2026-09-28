import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fmtDateTime, fmtRelative } from "@/lib/format";
import { useScraps, useToggleScrap, useToggleWatch, useWatchlist } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { CompanyAvatar, Empty, Icon, Skeleton } from "@/components/ui";
import "./profile.css";

/**
 * 관심 기업 · 스크랩한 뉴스 — 내 정보(MePage, /me)와 분리된 별도 화면(/me/watchlist).
 * 원래 내 페이지 한 화면에 프로필과 나란히 있었으나, 서로 다른 목적의 화면이라 분리했다.
 * 지금은 분리만 진행하고 레이아웃 재설계는 다음 단계 — 카드 내용·마크업은 기존 MePage 그대로 옮겼다.
 */
export default function MyItemsPage() {
  const status = useSession((s) => s.status);
  const user = useSession((s) => s.user);
  const openAuth = useUi((s) => s.openAuth);
  const navigate = useNavigate();
  const { hash } = useLocation();

  useEffect(() => {
    if (status === "guest") {
      openAuth("login");
      navigate("/", { replace: true });
    }
  }, [status, openAuth, navigate]);
  useEffect(() => {
    if (!hash) return;
    const el = document.querySelector(hash);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hash, status]);

  if (status !== "authed" || !user) return <div className="page">{status === "booting" && <Skeleton h={200} />}</div>;

  return (
    <div className="page me">
      <div className="page-head">
        <div>
          <span className="pill">watchlist</span>
          <h1>관심 기업 · 스크랩한 뉴스</h1>
        </div>
      </div>
      <div className="me-grid">
        <WatchCard />
        <ScrapCard />
      </div>
    </div>
  );
}

function WatchCard() {
  const watch = useWatchlist();
  const toggle = useToggleWatch();
  const navigate = useNavigate();
  const items = watch.data?.items ?? [];
  return (
    <section className="card me-card" id="watch">
      <div className="row between">
        <div>
          <div className="kick">watchlist</div>
          <h2 className="sect" style={{ fontSize: 20, marginTop: 6 }}>
            관심 기업 <span className="num" style={{ color: "var(--primary-2)" }}>{items.length}</span>
          </h2>
        </div>
        {/* 목록에서 바로 개인 은하로 — 관심 기업이 어떻게 이어지는지는 표가 아니라 은하가 보여 준다 */}
        <button type="button" className="btn btn-g btn-sm" onClick={() => navigate("/?scope=mine")}>
          <Icon.Galaxy /> 내 은하에서 보기
        </button>
      </div>
      <div className="stack mt-16">
        {watch.isLoading && <Skeleton h={60} />}
        {!watch.isLoading && !items.length && <Empty>은하에서 기업을 선택하고 ★ 관심 등록을 눌러보세요.</Empty>}
        {items.map((w) => (
          <div key={w.companyId} className="me-item">
            <CompanyAvatar name={w.name} stockCode={w.stockCode} market={w.market} industry={w.primaryIndustry?.name} size={40} />
            <button type="button" className="grow" style={{ textAlign: "left" }} onClick={() => navigate(`/?company=${w.companyId}`)}>
              <b>{w.name}</b>
              <span className="meta num">
                {[w.stockCode, w.primaryIndustry?.name, `${fmtRelative(w.watchedAt)} 등록`].filter(Boolean).join(" · ")}
              </span>
            </button>
            <button type="button" className="btn btn-g btn-xs" onClick={() => toggle.mutate({ companyId: w.companyId, watched: true })} aria-label="관심 해제">
              <Icon.Close />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function ScrapCard() {
  const scraps = useScraps();
  const toggle = useToggleScrap();
  const items = scraps.data?.items ?? [];
  return (
    <section className="card me-card" id="scraps">
      <div className="kick">scraps</div>
      <h2 className="sect" style={{ fontSize: 20, marginTop: 6 }}>
        스크랩한 뉴스 <span className="num" style={{ color: "var(--primary-2)" }}>{items.length}</span>
      </h2>
      <div className="stack mt-16">
        {scraps.isLoading && <Skeleton h={60} />}
        {!scraps.isLoading && !items.length && <Empty>뉴스 카드의 북마크 버튼으로 기사를 저장할 수 있습니다.</Empty>}
        {items.map((s) => (
          <div key={s.newsId} className="me-item" style={{ alignItems: "flex-start" }}>
            <div className="grow">
              <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.45 }}>{s.title}</div>
              <div className="meta num mt-8">
                {[s.publisher, s.publishedAt ? fmtDateTime(s.publishedAt) : null, `${fmtRelative(s.scrappedAt)} 저장`].filter(Boolean).join(" · ")}
              </div>
            </div>
            <a className="btn btn-g btn-xs" href={s.originalUrl} target="_blank" rel="noreferrer" aria-label="원문 보기">
              <Icon.External />
            </a>
            <button type="button" className="btn btn-g btn-xs" onClick={() => toggle.mutate({ newsId: s.newsId, scrapped: true })} aria-label="스크랩 해제">
              <Icon.Close />
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}
