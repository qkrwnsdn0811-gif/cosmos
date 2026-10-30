import { Component, lazy, Suspense, useEffect, useRef, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "@/api";
import Header from "@/components/Header";
import Toasts from "@/components/Toasts";
import AuthDialog from "@/features/auth/AuthDialog";
import InvestPage from "@/features/invest/InvestPage";
import MatchLauncher from "@/features/match/MatchLauncher";
import { GALAXY_PATH } from "@/lib/routes";
import { useSession } from "@/store/session";

// 이벤트(/: 뉴스 → 관계 지도 · 영향 종목)가 첫 화면이다 (2026-10-25, 명세서 F10) — 나머지 탭은 들어갈 때 받는다. 전부 정적으로 묶으면 첫 로드 번들에
// 뉴스·기업·커뮤니티·내 페이지·실험실 코드와 그 CSS 가 함께 실려 Lighthouse 가 "미사용 JS/CSS" 로 잡았다 (2026-09-18).
// 3D 은하는 탐색 화면(/galaxy)으로 옮기면서 지연 로드한다 — three.js 청크가 첫 화면 전송량에서 빠진다
const GalaxyPage = lazy(() => import("@/features/galaxy/GalaxyPage"));
const NewsPage = lazy(() => import("@/features/news/NewsPage"));
const PrivacyPage = lazy(() => import("@/features/legal/LegalPage").then((m) => ({ default: m.PrivacyPage })));
const TermsPage = lazy(() => import("@/features/legal/LegalPage").then((m) => ({ default: m.TermsPage })));
// 검색 팔레트의 "관계 검색"(기업 두 곳을 이어 주는 경로) 결과 화면 — 은하 페이지와 같은 Scene 청크를 쓰므로 여기서도 지연 로드한다
const PathGalaxyPage = lazy(() => import("@/features/galaxy/PathGalaxyPage"));
const CompaniesPage = lazy(() => import("@/features/companies/CompaniesPage"));
const CompanyPage = lazy(() => import("@/features/companies/CompanyPage"));
const CommunityPage = lazy(() => import("@/features/community/CommunityPage"));
const MePage = lazy(() => import("@/features/profile/MePage"));
const MyItemsPage = lazy(() => import("@/features/profile/MyItemsPage"));
const ArtLabPage = lazy(() => import("@/features/lab/ArtLabPage"));

/** 지연 로드 청크가 오는 동안(보통 100ms 안팎) 헤더 아래를 비워 두지 않는다 */
function RouteFallback() {
  return (
    <div className="route-fallback" aria-live="polite">
      불러오는 중
    </div>
  );
}

/** 예전 이벤트 지도 주소(/events?event=…)를 첫 화면으로 옮긴다. 검색 조건(event 등)은 그대로 넘긴다 */
function EventsRedirect() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: "/", search }} replace />;
}

/** 동적 import 실패 메시지 — 배포 직후 옛 index.html 이 남아 있으면 해시가 바뀐 청크가 404 다 (Chrome·Firefox·Safari 문구) */
const CHUNK_LOAD_ERROR = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Loading chunk/i;
const RELOAD_ONCE_KEY = "cosmos.chunk-reload";

/**
 * 지연 로드 경계. 라우트·Scene 을 lazy 로 나눈 뒤 생긴 실패 모드를 받는다: 배포로 청크 해시가 바뀐 뒤 옛 페이지에서 탭을 옮기면 import() 가
 * 404 로 실패하고, 경계가 없으면 React 가 트리 전체를 내려 빈 화면이 된다. 청크 로드 실패면 새 index.html 을 받도록 한 번만 새로고침하고
 * (sessionStorage 로 무한 새로고침을 막는다), 그 밖의 오류나 두 번째 실패는 다시 시도 버튼을 보인다.
 */
class LazyBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    if (!CHUNK_LOAD_ERROR.test(error.message)) {
      console.error(error, info.componentStack);
      return;
    }
    let reloaded = false;
    try {
      reloaded = sessionStorage.getItem(RELOAD_ONCE_KEY) === "1";
      if (!reloaded) sessionStorage.setItem(RELOAD_ONCE_KEY, "1");
    } catch {
      /* 저장 불가 환경 — 새로고침은 한 번 시도한다 */
    }
    if (!reloaded) window.location.reload();
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="route-fallback" role="alert">
        <div style={{ display: "grid", gap: 10, justifyItems: "center" }}>
          <span>화면을 불러오지 못했습니다.</span>
          <button type="button" className="btn btn-g btn-sm" onClick={() => window.location.reload()}>
            다시 시도
          </button>
        </div>
      </div>
    );
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      // 4xx 는 다시 보내도 같은 답이다(404 GRAPH_SNAPSHOT_NOT_FOUND·401 등) — 네트워크·5xx 만 1회 재시도한다
      retry: (count, err) => count < 1 && !(err instanceof ApiError && err.status < 500),
      refetchOnWindowFocus: false,
    },
  },
});

function SessionBoot() {
  const bootstrap = useSession((s) => s.bootstrap);
  const sessionKey = useSession((s) => s.sessionKey);
  const lastKey = useRef(sessionKey);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);
  // 로그인 사용자가 바뀌면 개인화 응답(watched·scrapped·personalized)을 다시 받는다.
  // 마운트 직후 한 번은 무효화할 캐시가 없으므로 건너뛴다 — 첫 로드 중복 호출의 원인이었다
  useEffect(() => {
    if (lastKey.current === sessionKey) return;
    lastKey.current = sessionKey;
    void queryClient.invalidateQueries();
  }, [sessionKey]);
  return null;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <SessionBoot />
        <div className="app">
          <Header />
          <LazyBoundary>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/" element={<InvestPage />} />
                <Route path={GALAXY_PATH} element={<GalaxyPage />} />
                <Route path="/path" element={<PathGalaxyPage />} />
                <Route path="/news" element={<NewsPage />} />
                {/* 뉴스 1건 3D 지도(/events)는 첫 화면에 합쳤다 — 예전 링크(?event=)는 그대로 첫 화면으로 */}
                <Route path="/events" element={<EventsRedirect />} />
                <Route path="/companies" element={<CompaniesPage />} />
                <Route path="/companies/:companyId" element={<CompanyPage />} />
                <Route path="/community" element={<CommunityPage />} />
                <Route path="/me" element={<MePage />} />
                {/* 관심 기업·스크랩한 뉴스 — 내 정보와 분리된 별도 화면. 지금은 분리만, 레이아웃 재설계는 다음 단계 */}
                <Route path="/me/watchlist" element={<MyItemsPage />} />
                {/* 아트 디렉션 실험 페이지 — 내비게이션 미노출 */}
                <Route path="/lab/art" element={<ArtLabPage />} />
                <Route path="/privacy" element={<PrivacyPage />} />
                <Route path="/terms" element={<TermsPage />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </LazyBoundary>
        </div>
        {/* 오른쪽 아래 '투자 성향 맞춤' — 로그인 창(AuthDialog)이 그 위에 뜨도록 앞에 둔다 */}
        <MatchLauncher />
        <AuthDialog />
        <Toasts />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
