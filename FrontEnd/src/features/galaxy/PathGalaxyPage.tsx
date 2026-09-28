import { lazy, Suspense, useEffect, useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { capScale, layoutPathGalaxy } from "@/lib/graph";
import { useLatestGraph } from "@/lib/queries";
import { useGalaxy } from "@/store/galaxy";
import { Icon } from "@/components/ui";
import CompanyPanel from "./CompanyPanel";
import RelationshipPanel from "./RelationshipPanel";
import SearchPalette from "./SearchPalette";
import "./galaxy.css";

const Scene = lazy(() => import("@/three/Scene"));

/**
 * 관계 검색 결과 — 검색 팔레트의 "관계 검색" 에서 기업을 두 번 고르면(?from=&to=) 여기로 온다.
 * 전체 은하와 같은 씬(Scene)을 재사용하되, 모델은 두 기업을 잇는 최단 경로만 한 줄로 배치한 것이다(lib/graph 의 layoutPathGalaxy).
 * 행성 클릭·호버는 전체 은하와 똑같이 전역 useGalaxy 스토어를 통해 동작한다(three/CompanyNodes 가 스토어를 직접 구독) — 이 화면은
 * "은하 범위"(전체/내 은하) 를 쓰지 않는 별도 페이지라 focusId·scope 는 건드리지 않고 선택 상태만 쓴다.
 */
export default function PathGalaxyPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const fromId = params.get("from");
  const toId = params.get("to");

  const selectedId = useGalaxy((s) => s.selectedId);
  const selectedEdgeId = useGalaxy((s) => s.selectedEdgeId);
  const panelOpen = useGalaxy((s) => s.panelOpen);
  const selectCompany = useGalaxy((s) => s.selectCompany);
  const setPanelOpen = useGalaxy((s) => s.setPanelOpen);
  const setSearchOpen = useGalaxy((s) => s.setSearchOpen);
  // ControlDock(메인 은하, 주가 고도 옆)에서 고른 관계선 기준 기간 — 화면을 옮겨도 store/galaxy 에 남아 있는 전역
  // 값이라, 이 화면도 같은 기준으로 다시 계산된 관계선 위에서 경로를 찾는다
  const edgeWindow = useGalaxy((s) => s.edgeWindow);

  // 이 화면을 떠나면(다른 경로 검색·뒤로 가기 등) 남아 있던 선택 상태가 다음 화면에 새지 않게 비운다
  useEffect(() => {
    return () => useGalaxy.getState().clearSelection();
  }, []);

  const universe = useLatestGraph(null, edgeWindow);
  const capT = useMemo(() => capScale(universe.data?.nodes ?? []), [universe.data]);
  const result = useMemo(() => {
    if (!universe.data || !fromId || !toId || fromId === toId) return null;
    return layoutPathGalaxy(universe.data, fromId, toId, capT);
  }, [universe.data, fromId, toId, capT]);
  const pathModel = result?.model ?? null;
  const hasPath = Boolean(result?.path?.length);

  /**
   * 예전엔 첫 번째 기업(from)의 인텔리전스 패널을 기본으로 열어 두었지만, 이 화면은 이제 모든 관계·역할 정보를
   * 기본값으로 보여주므로(three/emphasis.ts 의 isolate 모드 — 아래 <Scene isolateOnFocus>) 굳이 하나를 미리 고를
   * 필요가 없다. 기본 상태는 아무것도 선택돼 있지 않은 채로 두고, 사용자가 행성·관계를 눌러야 우측 패널이 뜬다.
   */

  /** 관계망을 더 보고 싶으면(패널의 "관계망 보기") 전체 은하로 나가 그 기업 중심 뷰를 연다 — 이 화면은 경로 한 줄만 보여준다 */
  const openInGalaxy = (id: string) => navigate(`/?company=${id}`);
  const pickCompany = (id: string) => (pathModel?.nodeById.has(id) ? selectCompany(id) : openInGalaxy(id));

  const loading = universe.isLoading;
  const invalidQuery = !fromId || !toId || fromId === toId;

  /* 우측 패널 내용 */
  const panelContent = selectedEdgeId ? (
    <RelationshipPanel relationshipId={selectedEdgeId} onClose={() => setPanelOpen(false)} onPickCompany={pickCompany} model={pathModel} />
  ) : (
    selectedId && <CompanyPanel companyId={selectedId} model={pathModel} onClose={() => selectCompany(null)} onPickCompany={pickCompany} onWarp={() => openInGalaxy(selectedId)} />
  );
  const panelActive = Boolean(panelOpen && (selectedId || selectedEdgeId));

  return (
    <div className={`galaxy${panelActive ? " hud-panel-active" : ""}`}>
      <div className="stage">
        <Suspense fallback={null}>
          {/* isolateOnFocus: 기본값은 모든 관계·역할 정보가 다 보이고, 호버·클릭한 것만 남기고 나머진 숨는다.
              panelBias: 우측에 뜨는 인텔리전스 패널 자리를 카메라 시선으로 미리 비워 둔다 */}
          <Scene galaxy={pathModel} system={null} next={null} isolateOnFocus panelBias={0.42} />
        </Suspense>
      </div>

      <div className={`stage-loading${loading ? "" : " off"}`} aria-live="polite" aria-hidden={!loading}>
        <span className="kick">news × relationship graph</span>
        <b>COSMOS</b>
        <i />
        <span>{loading ? "관계 경로를 찾는 중" : ""}</span>
      </div>
      {!loading && !invalidQuery && !hasPath && (
        <div className="stage-loading" style={{ pointerEvents: "auto" }}>
          <b>이어지는 관계를 찾지 못했습니다</b>
          <span>두 기업이 최신 공개 스냅샷 안에서 서로 연결돼 있지 않습니다.</span>
          <button type="button" className="btn btn-g btn-sm" onClick={() => navigate("/")}>
            <Icon.Back /> 전체 은하로
          </button>
        </div>
      )}
      {!loading && invalidQuery && (
        <div className="stage-loading" style={{ pointerEvents: "auto" }}>
          <b>검색한 기업 정보가 없습니다</b>
          <span>검색창에서 관계 검색으로 두 기업을 다시 골라 주세요.</span>
          <button type="button" className="btn btn-g btn-sm" onClick={() => navigate("/")}>
            <Icon.Back /> 전체 은하로
          </button>
        </div>
      )}

      {/* 좌상단: 뒤로 가기 + 경로 브레드크럼(A → B → C) + 검색(다른 경로를 바로 다시 찾도록 — GalaxyPage 기업 중심 뷰의 검색 아이콘과 같은 자리) */}
      <div className="hud hud-crumb">
        <div className="crumb">
          <button type="button" onClick={() => navigate("/")} title="전체 은하로 돌아가기">
            <Icon.Back /> 전체 은하
          </button>
          {hasPath && pathModel && (
            <>
              <span aria-hidden="true">›</span>
              {pathModel.nodes.map((n, i) => (
                <span key={n.id} className="row" style={{ gap: 6, display: "inline-flex", alignItems: "center" }}>
                  {i > 0 && <span aria-hidden="true">→</span>}
                  <b>{n.name}</b>
                </span>
              ))}
            </>
          )}
        </div>
        <button type="button" className="crumb" onClick={() => setSearchOpen(true)} title="기업·관계 검색">
          <Icon.Search />
        </button>
      </div>

      {(selectedId || selectedEdgeId) && !panelOpen && (
        <div className="hud hud-panel-tag">
          <button type="button" className="hud-tag vertical" onClick={() => setPanelOpen(true)} title="패널 펼치기">
            {selectedEdgeId ? "기업 관계" : (pathModel?.nodeById.get(selectedId ?? "")?.name ?? "") + " · 인텔리전스"}
          </button>
        </div>
      )}
      {panelActive && <div className="hud hud-panel">{panelContent}</div>}

      <SearchPalette onPick={pickCompany} />
    </div>
  );
}
