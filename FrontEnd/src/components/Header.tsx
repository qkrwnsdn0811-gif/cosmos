import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import type { Me } from "@/api";
import { HELP_UI } from "@/lib/guide";
import { useScraps, useWatchlist } from "@/lib/queries";
import { useNarrowViewport } from "@/lib/viewport";
import { useGalaxy } from "@/store/galaxy";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { cycleTabFocus, Icon, useClickOutside } from "./ui";

const NAV = [
  { to: "/", label: "전체 은하", end: true },
  { to: "/news", label: "뉴스" },
  { to: "/companies", label: "기업" },
  { to: "/community", label: "커뮤니티" },
];

/** 넓은 화면의 계정 열과 모바일 서랍이 같이 쓰는 세션·문맥 — Header 가 한 번 읽어 넘긴다 */
interface MenuContext {
  status: ReturnType<typeof useSession.getState>["status"];
  user: Me | null;
  pathname: string;
  /** '관심 기업 · 스크랩한 뉴스' 한 줄 — 두 수를 라벨 순서대로 나란히 보여 준다 */
  nWatch: number | "-";
  nScrap: number | "-";
  openAuth: (kind: "login" | "signup") => void;
  toggleHelp: () => void;
  /** 로그아웃 + 토스트 */
  signOut: () => Promise<void>;
}

/**
 * 헤더 — 탭 + 게스트/로그인 2가지 상태 (화면구성도 ⑩).
 * 넓은 화면은 로고 · 탭(정중앙) · ?/계정(오른쪽) 3열이고, 모바일(900px 이하, lib/viewport)은 로고 · ☰ 둘만 남기고
 * 탭·사용 안내·계정 항목을 ☰ 아래 서랍 하나(MobileMenu)로 모은다 — 좁은 폭에 탭 4개와 로그인·회원가입이 한 줄로 끼지 않게.
 */
export default function Header() {
  const status = useSession((s) => s.status);
  const user = useSession((s) => s.user);
  const logout = useSession((s) => s.logout);
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const toggleHelp = useUi((s) => s.toggleHelp);
  const { pathname } = useLocation();
  const narrow = useNarrowViewport();
  const watchlist = useWatchlist();
  const scraps = useScraps();
  const nWatch = watchlist.data?.items.length ?? "-";
  const nScrap = scraps.data?.items.length ?? "-";

  const signOut = useCallback(async () => {
    await logout();
    toast("로그아웃했습니다.");
  }, [logout, toast]);

  const ctx: MenuContext = { status, user, pathname, nWatch, nScrap, openAuth, toggleHelp, signOut };

  return (
    <header className={narrow ? "hd hd-narrow" : "hd"}>
      {/* "/" 는 전체 은하·기업 중심 뷰가 같은 경로(query only)를 쓰므로, 기업 중심 뷰(focusId 있음)에서 로고를 눌러도
          React Router 입장에서는 route 전환이 아니라 그대로다 — GalaxyPage 가 리마운트되지 않아 화면이 안 바뀌었다.
          로고를 누르면 늘 전체 은하로 돌아가도록 기존 "은하로 돌아가기" 버튼과 같은 backToGalaxy(warpTo(null))를 같이 호출한다 */}
      <NavLink
        to="/"
        className="logo"
        aria-label="COSMOS 홈"
        onClick={() => {
          const s = useGalaxy.getState();
          if (s.focusId !== null || s.phase !== "galaxy") s.backToGalaxy();
        }}
      >
        <img src="/cosmos-logo.png" alt="" width={167} height={128} />
        <span>COSMOS</span>
      </NavLink>
      {narrow ? (
        <MobileMenu {...ctx} />
      ) : (
        <>
          <nav aria-label="주요 메뉴">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "on" : "")}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <DesktopAccount {...ctx} />
        </>
      )}
    </header>
  );
}

/** 넓은 화면의 오른쪽 열 — `?`(은하 화면에서만) · 프로필 버튼과 그 아래 메뉴, 게스트면 로그인·회원가입 */
function DesktopAccount({ status, user, pathname, nWatch, nScrap, openAuth, toggleHelp, signOut }: MenuContext) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const menuRef = useClickOutside<HTMLDivElement>(close, open);

  return (
    <div className="account" ref={menuRef} style={{ position: "relative" }}>
      {/* 사용 안내(`?`) — 은하 화면의 읽는 법·조작법 허브. 허브 자체는 GalaxyPage 가 그리므로 그 화면에서만 보인다 */}
      {pathname === "/" && (
        <button type="button" className="help-btn" onClick={toggleHelp} aria-label={HELP_UI.button} title={HELP_UI.buttonTitle}>
          <Icon.Help />
        </button>
      )}
      {/* 세션이 정해지기 전에는 비워 둔다 — 로그인 사용자에게 로그인·회원가입 버튼이 한 번 번쩍이지 않게 */}
      {status === "booting" ? null : status === "authed" && user ? (
        <>
          <button type="button" className="profile-btn" onClick={() => setOpen((v) => !v)} aria-haspopup="menu" aria-expanded={open}>
            <span className="avatar">{user.nickname.slice(0, 1)}</span>
            <Icon.Chevron />
          </button>
          {open && (
            <div className="card profile-menu" role="menu">
              <div className="who">
                <b>{user.nickname}</b>
                <span className="meta">{user.email}</span>
              </div>
              <div style={{ padding: "6px 0" }}>
                <button type="button" className="item on" role="menuitem" onClick={() => (close(), navigate("/me"))}>
                  <span style={{ fontWeight: 600 }}>내 페이지</span>
                </button>
                {/* 탭은 3개로 두기로 했으므로 개인 은하 진입은 프로필 메뉴에서 연다 */}
                <button type="button" className="item" role="menuitem" onClick={() => (close(), navigate("/?scope=mine"))}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <Icon.Galaxy /> 내 은하
                  </span>
                </button>
                {/* 관심 기업·스크랩은 내 정보와 분리된 별도 화면(/me/watchlist)이다 — 한 줄로 합쳐 이동만 같이 한다 */}
                <button type="button" className="item" role="menuitem" onClick={() => (close(), navigate("/me/watchlist"))} title={`관심 기업 ${nWatch}개 · 스크랩한 뉴스 ${nScrap}개`}>
                  <span>관심 기업 · 스크랩한 뉴스</span>
                  <span className="num">{`${nWatch} · ${nScrap}`}</span>
                </button>
              </div>
              <div className="divider" style={{ padding: "6px 0 0" }}>
                <button type="button" className="item" role="menuitem" onClick={() => (close(), void signOut())}>
                  <span style={{ color: "var(--text-3)" }}>로그아웃</span>
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <button type="button" className="login-link" onClick={() => openAuth("login")}>
            로그인
          </button>
          <button type="button" className="btn btn-p" style={{ padding: "9px 15px" }} onClick={() => openAuth("signup")}>
            회원가입
          </button>
        </>
      )}
    </div>
  );
}

/**
 * 모바일 ☰ 서랍 — 탭 4개 · (은하 화면) 사용 안내 · 계정(내 페이지 · 내 은하 · 관심·스크랩 · 로그아웃, 게스트면 로그인 · 회원가입)을 한 장에.
 * 열린 경로를 기억해 두고 지금 경로와 다르면 닫힌 것으로 본다 — 뒤로가기처럼 서랍 밖에서 화면이 바뀌어도 effect 없이 저절로 닫힌다.
 * 항목을 누르면 바로 닫고, 스크림 탭·☰ 재탭·ESC 로도 닫는다. 헤더는 그대로 두고 그 아래에만 스크림을 깐다.
 */
function MobileMenu({ status, user, pathname, nWatch, nScrap, openAuth, toggleHelp, signOut }: MenuContext) {
  const navigate = useNavigate();
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;
  const close = useCallback(() => setOpenAt(null), []);
  const ref = useClickOutside<HTMLDivElement>(close, open);
  const firstItem = useRef<HTMLAnchorElement>(null);
  /* 열리면 첫 항목으로 포커스를 옮긴다 — 다이얼로그 관례이고, 그래야 ESC 가 서랍 안에서 잡힌다 (React 의 autoFocus 는 a 요소에는 듣지 않는다) */
  useEffect(() => {
    if (open) firstItem.current?.focus();
  }, [open]);
  /** 항목 하나를 실행하고 서랍을 닫는다 */
  const run = (fn: () => void) => () => {
    close();
    fn();
  };
  /* ESC 는 서랍만 닫는다 — GalaxyPage 의 전역 ESC(한 겹 닫기)까지 내려가지 않게 여기서 멈춘다.
     Tab 은 ☰ 버튼과 서랍 항목 안에서만 돈다 — 스크림 뒤의 HUD(접힌 도크 '^' 등)로 포커스가 새면 ESC 가 이 래퍼에 닿지 않아 닫을 길이 없어진다 */
  function onKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (!open) return;
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    cycleTabFocus(e, e.currentTarget);
  }

  return (
    <div className="account" ref={ref} onKeyDown={onKeyDown}>
      <button type="button" className="menu-btn" onClick={() => setOpenAt(open ? null : pathname)} aria-label={open ? "메뉴 닫기" : "메뉴"} aria-expanded={open} aria-controls="mobile-menu">
        {open ? <Icon.Close /> : <Icon.Menu />}
      </button>
      {open && (
        <>
          <div className="nav-scrim" onClick={close} aria-hidden="true" />
          {/* aria-modal 은 다른 오버레이와 같은 약속이다 — three/keyboard 의 modalOpen() 이 이 속성으로 '모달 열림' 을 알아채 OrbitKeys(방향키·WASD 카메라)와 ? 단축키를 멈춘다 */}
          <div className="card nav-drawer" id="mobile-menu" role="dialog" aria-modal="true" aria-label="메뉴">
            <nav aria-label="주요 메뉴" className="drawer-group">
              {NAV.map((n, i) => (
                <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "item on" : "item")} onClick={close} ref={i === 0 ? firstItem : undefined}>
                  {n.label}
                </NavLink>
              ))}
              {/* 사용 안내(`?`) — 넓은 화면의 헤더 버튼과 같은 허브. 허브는 GalaxyPage 가 그리므로 은하 화면에서만 */}
              {pathname === "/" && (
                <button type="button" className="item" onClick={run(toggleHelp)} title={HELP_UI.buttonTitle}>
                  <span className="item-ico">
                    <Icon.Help /> {HELP_UI.button}
                  </span>
                </button>
              )}
            </nav>
            {/* 세션이 정해지기 전에는 비워 둔다 — 넓은 화면의 계정 열과 같은 이유 */}
            {status === "booting" ? null : status === "authed" && user ? (
              <div className="drawer-group divider">
                <div className="who">
                  <b>{user.nickname}</b>
                  <span className="meta">{user.email}</span>
                </div>
                <button type="button" className="item" onClick={run(() => navigate("/me"))}>
                  내 페이지
                </button>
                <button type="button" className="item" onClick={run(() => navigate("/?scope=mine"))}>
                  <span className="item-ico">
                    <Icon.Galaxy /> 내 은하
                  </span>
                </button>
                <button type="button" className="item" onClick={run(() => navigate("/me/watchlist"))} title={`관심 기업 ${nWatch}개 · 스크랩한 뉴스 ${nScrap}개`}>
                  <span>관심 기업 · 스크랩한 뉴스</span>
                  <span className="num">{`${nWatch} · ${nScrap}`}</span>
                </button>
                <button type="button" className="item dim" onClick={run(() => void signOut())}>
                  로그아웃
                </button>
              </div>
            ) : (
              <div className="drawer-auth divider">
                <button type="button" className="btn btn-g" onClick={run(() => openAuth("login"))}>
                  로그인
                </button>
                <button type="button" className="btn btn-p" onClick={run(() => openAuth("signup"))}>
                  회원가입
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
