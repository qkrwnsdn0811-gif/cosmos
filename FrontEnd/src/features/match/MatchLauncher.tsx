import { lazy, Suspense, useState } from "react";
import { useLocation } from "react-router-dom";
import { GALAXY_PATH } from "@/lib/routes";
import { TYPE_INFO, loadMatch, type SavedMatch } from "./investorMatch";
import "./match.css";

// 설문·결과 창은 누를 때 받는다 — 첫 화면 번들에는 버튼만 실린다
const MatchDialog = lazy(() => import("./MatchDialog"));

/** 3D 은하 화면은 오른쪽 아래에 패널·조작부가 있어 버튼을 띄우지 않는다 */
const HIDDEN_PATHS = [GALAXY_PATH, "/path"];

/** 오른쪽 아래 '투자 성향 맞춤' 버튼. 마우스를 올리면 설명이 뜨고, 누르면 진단(또는 지난 결과)을 연다 */
export default function MatchLauncher() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<SavedMatch | null>(() => loadMatch());
  if (HIDDEN_PATHS.includes(pathname)) return null;
  return (
    <>
      <div className="ml-wrap">
        <span className="ml-tip" role="tooltip" id="ml-tip">
          투자 성향에 맞는 종목을 골라 드려요
          {saved && <small>내 성향: {TYPE_INFO[saved.type].label} · {saved.horizon === "SHORT" ? "단기" : "장기"}</small>}
        </span>
        <button type="button" className="ml-btn" aria-describedby="ml-tip" aria-label="투자 성향 맞춤 종목 열기" onClick={() => setOpen(true)}>
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M9 11l3 3 8-8" />
            <path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" />
          </svg>
          {saved && <span className="ml-dot" aria-hidden="true" />}
        </button>
      </div>
      {open && (
        <Suspense fallback={null}>
          <MatchDialog
            initial={saved}
            onSaved={setSaved}
            onClose={() => setOpen(false)}
          />
        </Suspense>
      )}
    </>
  );
}
