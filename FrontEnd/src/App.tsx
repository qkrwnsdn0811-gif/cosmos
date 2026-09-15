import { useEffect, useRef } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Header from "@/components/Header";
import Toasts from "@/components/Toasts";
import AuthDialog from "@/features/auth/AuthDialog";
import GalaxyPage from "@/features/galaxy/GalaxyPage";
import NewsPage from "@/features/news/NewsPage";
import CompaniesPage from "@/features/companies/CompaniesPage";
import MePage from "@/features/profile/MePage";
import ArtLabPage from "@/features/lab/ArtLabPage";
import { useSession } from "@/store/session";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false },
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
          <Routes>
            <Route path="/" element={<GalaxyPage />} />
            <Route path="/news" element={<NewsPage />} />
            <Route path="/companies" element={<CompaniesPage />} />
            <Route path="/me" element={<MePage />} />
            {/* 아트 디렉션 실험 페이지 — 내비게이션 미노출 */}
            <Route path="/lab/art" element={<ArtLabPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
        <AuthDialog />
        <Toasts />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
