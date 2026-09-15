import { useCallback, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { API_MODE } from "@/api";
import { HELP_UI } from "@/lib/guide";
import { useScraps, useWatchlist } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { Icon, useClickOutside } from "./ui";

const NAV = [
  { to: "/", label: "전체 은하", end: true },
  { to: "/news", label: "뉴스" },
  { to: "/companies", label: "기업" },
];

/** 헤더 — 탭 3개 + 게스트/로그인 2가지 상태 (화면구성도 ⑩) */
export default function Header() {
  const status = useSession((s) => s.status);
  const user = useSession((s) => s.user);
  const logout = useSession((s) => s.logout);
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const toggleHelp = useUi((s) => s.toggleHelp);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const menuRef = useClickOutside<HTMLDivElement>(close, open);
  const watchlist = useWatchlist();
  const scraps = useScraps();

  return (
    <header className="hd">
      <NavLink to="/" className="logo" aria-label="COSMOS 홈">
        <img src="/cosmos-logo.png" alt="" />
        <span>COSMOS</span>
        {API_MODE === "mock" && (
          <span className="mode-badge" title="백엔드 없이 브라우저 안에서 생성한 목업 데이터입니다">
            MOCK
          </span>
        )}
      </NavLink>
      <nav aria-label="주요 메뉴">
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "on" : "")}>
            {n.label}
          </NavLink>
        ))}
      </nav>
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
                  <button type="button" className="item" role="menuitem" onClick={() => (close(), navigate("/me#watch"))}>
                    <span>관심 기업</span>
                    <span className="num">{watchlist.data?.items.length ?? "-"}</span>
                  </button>
                  <button type="button" className="item" role="menuitem" onClick={() => (close(), navigate("/me#scraps"))}>
                    <span>스크랩한 뉴스</span>
                    <span className="num">{scraps.data?.items.length ?? "-"}</span>
                  </button>
                </div>
                <div className="divider" style={{ padding: "6px 0 0" }}>
                  <button
                    type="button"
                    className="item"
                    role="menuitem"
                    onClick={async () => {
                      close();
                      await logout();
                      toast("로그아웃했습니다.");
                    }}
                  >
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
    </header>
  );
}
