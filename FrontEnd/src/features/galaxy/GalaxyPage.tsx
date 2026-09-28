import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/api";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { capScale, layoutGalaxy, layoutMyGalaxy, layoutSystemCached, type CompanyLookup } from "@/lib/graph";
import { fmtDateTime } from "@/lib/format";
import { companyGraphQuery, useCompanyGraph, useIndustries, useLatestGraph, useSessionKey, useToggleWatch, useWatchlist } from "@/lib/queries";
import { useGalaxy, type GalaxyScope } from "@/store/galaxy";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { useSceneReady } from "@/three/sceneReady";
import { Icon } from "@/components/ui";
import CameraPresets from "./CameraPresets";
import CompanyPanel from "./CompanyPanel";
import ControlDock from "./ControlDock";
import HelpHub, { HelpHotkey } from "./HelpHub";
import HoldRing from "./HoldRing";
import HopSummary from "./HopSummary";
import IndustryPicker from "./IndustryPicker";
import MyGalaxyBar, { MyGalaxyEmpty } from "./MyGalaxyBar";
import PathStrip from "./PathStrip";
import RelationshipPanel from "./RelationshipPanel";
import SceneTip, { type PointerPos } from "./SceneTip";
import SearchPalette from "./SearchPalette";
import SurpriseRail from "./SurpriseRail";
import Tour, { isTourDone } from "./Tour";
import "./galaxy.css";

// 씬(three 청크 1.2MB)은 HUD 뒤에 받는다 — 정적으로 묶었을 때는 헤더·카드의 첫 페인트가 three 평가가 끝날 때까지 밀렸다 (2026-09-18 Lighthouse LCP 1.8s).
// 청크 자체는 vite.config 의 preloadSceneChunks 가 HTML 에서 미리 받게 하므로 다운로드가 늦어지지는 않는다
const Scene = lazy(() => import("@/three/Scene"));

/** 호버 200ms 프리페치 — 워프 커밋(0.6s) 전에 목적지 배치가 준비되도록 미리 받아 둔다.
    hoveredId 구독을 이 조각에 가둬 호버마다 HUD 전체가 리렌더되지 않게 한다 */
function HoverPrefetch({ focusId }: { focusId: string | null }) {
  const hoveredId = useGalaxy((s) => s.hoveredId);
  const edgeWindow = useGalaxy((s) => s.edgeWindow);
  const sessionKey = useSessionKey();
  const qc = useQueryClient();
  useEffect(() => {
    if (!hoveredId || hoveredId === focusId) return;
    const t = setTimeout(() => void qc.prefetchQuery(companyGraphQuery(sessionKey, hoveredId, edgeWindow)), 200);
    return () => clearTimeout(t);
  }, [hoveredId, focusId, sessionKey, edgeWindow, qc]);
  return null;
}

/**
 * 전체 은하 (화면구성도 ①). 산업 성단 위에 기업 항성과 관계 간선을 그리고,
 * 노드를 누르면 워프해 그 기업 중심 관계망(depth 궤도)으로 들어간다.
 */
export default function GalaxyPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  /**
   * 뉴스 페이지의 관계 줄 "전체 은하에서 보기"에서 온 경우, 그 뉴스 화면(선택했던 기사·필터)으로
   * 되돌아갈 경로를 함께 받는다(NewsPage.tsx 의 navigate state). 마운트 시점 값만 기억해 두고,
   * 이후 이 화면 안에서 URL 이 계속 바뀌어도(?edge= 소비 등) 사라지지 않게 state 에 한 번만 담는다.
   * (렌더 중 읽어야 하므로 ref 대신 지연 초기화 state 를 쓴다 — ref.current 를 렌더 중에 읽는 것은 React 규칙 위반이다)
   */
  const [newsReturnTo] = useState((location.state as { newsReturnTo?: string } | null)?.newsReturnTo ?? null);
  /** 위 안내 팝업을 사용자가 X 로 닫았는지 — 닫으면 이번 방문 동안은 다시 띄우지 않는다 */
  const [newsReturnDismissed, setNewsReturnDismissed] = useState(false);

  const scope = useGalaxy((s) => s.scope);
  const industryId = useGalaxy((s) => s.industryId);
  const focusId = useGalaxy((s) => s.focusId);
  const phase = useGalaxy((s) => s.phase);
  const selectedEdgeId = useGalaxy((s) => s.selectedEdgeId);
  const selectedId = useGalaxy((s) => s.selectedId);
  const selectCompany = useGalaxy((s) => s.selectCompany);
  const setScope = useGalaxy((s) => s.setScope);
  const setIndustry = useGalaxy((s) => s.setIndustry);
  const warpTo = useGalaxy((s) => s.warpTo);
  const selectEdge = useGalaxy((s) => s.selectEdge);
  const setSearchOpen = useGalaxy((s) => s.setSearchOpen);
  const panelOpen = useGalaxy((s) => s.panelOpen);
  const setPanelOpen = useGalaxy((s) => s.setPanelOpen);
  // 관계선(간선) 계산 기준 기간 — ControlDock 의 주가 고도 옆 버튼이 고른다. 메인 은하·기업 중심 뷰가 요청하는
  // 그래프에 그대로 실려 백엔드가 이 기간 기준으로 다시 계산한 edges 를 돌려준다
  const edgeWindow = useGalaxy((s) => s.edgeWindow);

  const status = useSession((s) => s.status);
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const industries = useIndustries();
  const universe = useLatestGraph(null); // 전체 우주 — 색·시장 조회용. 노드만 쓰고 간선을 그리지 않으므로 edgeWindow 를 넘기지 않는다
  const latest = useLatestGraph(industryId, edgeWindow);
  const focusGraph = useCompanyGraph(focusId, edgeWindow);
  // 워프 중인 목적지 — 커밋 전에 미리 배치를 만들어 둬야 모프가 첫 프레임부터 이어진다
  const warpTarget = useGalaxy((s) => s.warpTarget);
  const targetGraph = useCompanyGraph(phase === "warp" ? warpTarget : null, edgeWindow);
  const watchlist = useWatchlist();
  const toggleWatch = useToggleWatch();
  const mineScope = scope === "mine";
  const watchItems = useMemo(() => watchlist.data?.items ?? [], [watchlist.data]);
  const watchIds = useMemo(() => watchItems.map((w) => w.companyId), [watchItems]);

  // 시가총액 척도는 전체 우주 기준으로 한 번 만들어 은하 배치와 기업 중심 배치(lookup)에 같이 준다 — 산업 필터가 켜져도 같은 기업은 같은 크기
  const capT = useMemo(() => capScale(universe.data?.nodes ?? []), [universe.data]);
  const fullModel = useMemo(() => (latest.data ? layoutGalaxy(latest.data, capT) : null), [latest.data, capT]);
  // 내 은하는 전체 우주에서 관심 기업 주변만 잘라 같은 배치로 만든다 — 산업 필터(latest)가 아니라 우주(universe)가 재료다.
  // 이웃(1홉) 포함은 항상 켜져 있다 — 끄는 기능은 없앴다(관심 기업만 보면 어떻게 이어지는지 알 수 없어 혼란을 줬다)
  const mine = useMemo(() => (mineScope && universe.data ? layoutMyGalaxy(universe.data, watchIds, true, capT) : null), [mineScope, universe.data, watchIds, capT]);
  // 별이 하나도 없으면 씬에 null 을 준다 — 빈 원반을 그리는 대신 빈 상태 카드가 화면을 설명한다
  const galaxyModel = mineScope ? (mine && mine.model.nodes.length ? mine.model : null) : fullModel;
  const lookup = useMemo<CompanyLookup>(() => {
    const nodes = universe.data?.nodes ?? [];
    const m = new Map(nodes.map((n) => [n.companyId, { name: n.name, industryName: n.industryName, market: n.market, stockCode: n.stockCode, capT: capT(n.marketCapKrw) }]));
    return (id: string) => m.get(id);
  }, [universe.data, capT]);
  // 그래프 객체 기준 캐시(layoutSystemCached) — 커밋 뒤 목적지 그래프가 포커스 그래프가 되어도 같은 SceneModel 이라
  // 씬이 리마운트되지 않고(팝인 없음) 모프가 그대로 이어진다
  const systemModel = useMemo(() => (focusGraph.data ? layoutSystemCached(focusGraph.data, lookup) : null), [focusGraph.data, lookup]);
  const nextModel = useMemo(() => (targetGraph.data ? layoutSystemCached(targetGraph.data, lookup) : null), [targetGraph.data, lookup]);
  // 씬 툴팁이 따라갈 포인터 위치 — ref 에만 써서 포인터 이동이 리렌더를 만들지 않는다
  const pointer = useRef<PointerPos>({ x: -1000, y: -1000 });

  /* 첫 방문 투어 자동 시작 — 딥링크(?company=·?edge=)나 내 은하(?scope=mine)로 들어온 진입은 그 화면이 먼저다. 마운트 시점에 한 번 정한다 */
  const [autoTour] = useState(() => !params.get("company") && !params.get("edge") && params.get("scope") !== "mine");
  /** 첫 방문 투어가 자동으로 뜰 조건 — Tour.tsx 의 자동 시작 조건과 같다. 그 인트로 딤(72% 어둡게)이 씬을 덮으므로 워밍업 오버레이를 따로 붙잡을 필요가 없다 */
  const [tourWillCover] = useState(() => autoTour && !isTourDone());
  const helpOpen = useUi((s) => s.helpOpen);

  /* URL ?company= 와 포커스 동기화 — 진입 시 딥링크를 먼저 소비한 뒤에만 URL 을 상태에 맞춘다 */
  const pendingDeepLink = useRef(params.get("company"));
  useEffect(() => {
    const requested = pendingDeepLink.current;
    if (!requested || !galaxyModel) return;
    pendingDeepLink.current = null;
    if (requested !== useGalaxy.getState().focusId) warpTo(requested);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [galaxyModel]);
  useEffect(() => {
    if (pendingDeepLink.current) return;
    const cur = params.get("company");
    if ((focusId ?? null) === (cur ?? null)) return;
    const next = new URLSearchParams(params);
    if (focusId) next.set("company", focusId);
    else next.delete("company");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusId]);

  /* URL ?edge= — 뉴스 페이지 관계 줄의 은하 버튼("전체 은하에서 보기")에서 들어오는 딥링크.
     RelationshipPanel 은 relationshipId로 서버에서 직접 조회하므로(모델은 "왜 특별한가" 설명에만
     쓰고 없어도 동작한다) ?company= 와 달리 galaxyModel 을 기다릴 필요가 없다. 간선 선택은 이 화면
     안에서 URL 과 계속 동기화되는 상태가 아니라서(3D 씬에서 다른 간선을 눌러도 URL은 그대로다),
     ?company= 처럼 양방향으로 두지 않고 진입 시 한 번만 선택한 뒤 파라미터를 지운다. "전체 은하"에서
     본 것과 같은 화면이 뜨도록 범위도 함께 전체로 되돌린다(내 은하로 들어와 있던 세션이면). */
  const pendingEdgeLink = useRef(params.get("edge"));
  useEffect(() => {
    const requested = pendingEdgeLink.current;
    if (!requested) return;
    pendingEdgeLink.current = null;
    if (useGalaxy.getState().scope !== "all") setScope("all");
    selectEdge(requested);
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("edge");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* URL ?scope=mine — 세션이 정해진 뒤 소비한다. 게스트면 로그인 안내만 하고 파라미터를 지운다(전체 은하 유지)
     params 자체를 구독해야, 은하 페이지에 이미 마운트된 채로 헤더 "내 은하"처럼 쿼리만 바뀌는 이동도 소비된다 */
  /* 게스트가 내 은하를 요청(딥링크·토글)한 뒤 로그인하면 바로 그 화면으로 — 다이얼로그를 그냥 닫으면 의도도 접는다 */
  const wantMine = useRef(false);
  const authDialog = useUi((s) => s.authDialog);
  useEffect(() => {
    if (status === "authed") {
      if (wantMine.current) {
        wantMine.current = false;
        setScope("mine");
      }
    } else if (authDialog === null) wantMine.current = false;
  }, [status, authDialog, setScope]);
  /* 진입 시 ?scope=mine 이 있으면 세션 판정(booting)이 끝날 때까지 아래 URL 동기화가 파라미터를 지우지 못하게 막는다 — ?company= 의 pendingDeepLink 와 같은 역할 */
  const pendingScope = useRef(params.get("scope") === "mine");
  useEffect(() => {
    if (params.get("scope") !== "mine" || status === "booting") return;
    pendingScope.current = false;
    if (status === "authed") {
      if (useGalaxy.getState().scope !== "mine") setScope("mine");
      return;
    }
    wantMine.current = true;
    openAuth("login");
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("scope");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, params]);
  useEffect(() => {
    if (pendingScope.current) return;
    const want = scope === "mine" ? "mine" : null;
    if ((params.get("scope") ?? null) === want) return;
    // 함수형 갱신 — ?company= 동기화와 같은 커밋에 겹쳐도 서로의 파라미터를 덮지 않는다
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (want) next.set("scope", want);
        else next.delete("scope");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);
  /* 로그아웃하면 관심 목록이 사라져 내 은하를 그릴 재료가 없다 — 전체 은하로 돌려놓는다(위 동기화가 파라미터도 지운다) */
  useEffect(() => {
    if (status === "guest" && useGalaxy.getState().scope === "mine") setScope("all");
  }, [status, setScope]);

  /* 키보드: ESC — 도움말 허브 → 산업 선택판 → 역할 필터 → 경로 기준 핀 → 선택 간선 → 은하 복귀 순으로 한 겹씩 벗긴다 */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const s = useGalaxy.getState();
      if (e.key === "Escape" && !s.searchOpen) {
        const ui = useUi.getState();
        if (ui.helpOpen) ui.closeHelp();
        else if (s.industryOpen) s.setIndustryOpen(false);
        else if (s.roleFilter) s.setRoleFilter(null);
        else if (s.selectedEdgeId) selectEdge(null);
        else if (s.selectedId) s.selectCompany(null);
        else if (s.focusId && s.phase !== "warp") s.backToGalaxy();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectEdge]);

  const pickCompany = useCallback(
    (id: string) => {
      // 산업 필터 밖의 기업이면 필터를 풀고 이동한다 (내 은하는 산업 필터를 쓰지 않으므로 건너뛴다)
      const inFilter = mineScope || !industryId || latest.data?.nodes.some((n) => n.companyId === id);
      if (!inFilter) setIndustry(null);
      warpTo(id);
    },
    [mineScope, industryId, latest.data, setIndustry, warpTo],
  );

  /** 검색으로 고른 기업 — 내 은하에서는 아직 관심 기업이 아니면 먼저 담아 그 자리에 별이 생기게 한다 */
  const pickFromSearch = useCallback(
    (id: string) => {
      if (mineScope && status === "authed" && !watchIds.includes(id)) {
        toggleWatch.mutate({ companyId: id, watched: false }, { onSuccess: () => toast("내 은하에 추가했습니다.", "success") });
      }
      pickCompany(id);
    },
    [mineScope, status, watchIds, toggleWatch, toast, pickCompany],
  );

  /**
   * '기업 추가'(MyGalaxyBar, 우측 상단) 전용 — 좌측 상단 검색창(pickFromSearch)과는 별도 로직이다.
   * 은하로 이동(warpTo)하지 않고 관심 등록만 한다 — 목록이 다시 불러와지면(useToggleWatch) 내 은하가 그 자리에서 새로고침된다.
   */
  const addToMyGalaxy = useCallback(
    (id: string) => {
      if (watchIds.includes(id)) return;
      toggleWatch.mutate({ companyId: id, watched: false }, { onSuccess: () => toast("내 은하에 추가했습니다.", "success"), onError: () => toast("추가하지 못했습니다.", "error") });
    },
    [watchIds, toggleWatch, toast],
  );

  /** 범위 전환 — 게스트가 내 은하를 누르면 로그인부터. 산업 칩과 같이 기업 중심 뷰였다면 은하로 되돌린다 */
  const pickScope = useCallback(
    (next: GalaxyScope) => {
      if (next === "mine" && status !== "authed") {
        wantMine.current = true;
        openAuth("login");
        return;
      }
      if (next === useGalaxy.getState().scope) return;
      setScope(next);
      if (focusId) warpTo(null);
    },
    [status, openAuth, setScope, focusId, warpTo],
  );

  const focusName = focusId ? systemModel?.nodeById.get(focusId)?.name ?? lookup(focusId)?.name ?? "…" : null;

  /* 우측 패널 내용 */
  const panelContent = selectedEdgeId ? (
    <RelationshipPanel relationshipId={selectedEdgeId} onClose={() => setPanelOpen(false)} onPickCompany={pickCompany} model={focusId ? systemModel : galaxyModel} />
  ) : focusId ? (
    <CompanyPanel companyId={focusId} onClose={() => setPanelOpen(false)} onPickCompany={pickCompany} />
  ) : (
    selectedId && (
      <CompanyPanel
        companyId={selectedId}
        model={galaxyModel}
        onClose={() => selectCompany(null)}
        onPickCompany={(id) => (galaxyModel?.nodeById.has(id) ? selectCompany(id) : pickCompany(id))}
        onWarp={() => pickCompany(selectedId)}
      />
    )
  );
  const panelActive = Boolean(panelOpen && (focusId || selectedEdgeId || selectedId));
  // 내 은하의 기준 스냅샷은 우주다 — 상태 카드의 시각·갱신 예정도 그 재료를 가리켜야 한다
  const snapshot = mineScope ? universe.data : latest.data;
  const nextRefreshAt = snapshot?.nextRefreshAt;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!nextRefreshAt) return;
    // 갱신 예정 시각을 넘을 때 상태 표시를 갱신하고, 스냅샷 변경 시 예약을 교체한다.
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, Date.parse(nextRefreshAt) - Date.now() + 1));
    return () => clearTimeout(timer);
  }, [nextRefreshAt]);
  const stale = nextRefreshAt ? now > Date.parse(nextRefreshAt) : false;
  const scopeLoading = mineScope ? universe.isLoading || watchlist.isLoading : latest.isLoading;
  const loading = scopeLoading || (Boolean(focusId) && focusGraph.isLoading && !systemModel);
  // 모델은 왔지만 씬이 첫 프레임을 매끄럽게 낼 준비(셰이더 컴파일·텍스처 업로드 — Scene.tsx Director 의 워밍업)가 안 됐으면 로딩 화면을 조금 더 붙잡는다.
  // 예전에는 데이터 도착 즉시 걷혀 그 컴파일 멈춤(120~370ms)이 인트로 비행 첫 장면의 끊김으로 그대로 보였다
  // 첫 방문에는 투어 인트로가 모델 도착과 함께 떠서 그 뒤를 덮는다 — 이때 오버레이까지 붙잡으면 인트로 문구(이 화면의 LCP 요소)만 늦어진다
  const sceneReady = useSceneReady();
  const warming = Boolean(galaxyModel) && !sceneReady && !(tourWillCover && scope === "all" && !focusId);
  const showLoading = loading || warming;
  const scopeError = mineScope ? universe.isError : latest.isError;
  // 공개된 스냅샷이 아직 없는 것(404 GRAPH_SNAPSHOT_NOT_FOUND)은 장애가 아니다 — 분석 결과가 게시되기 전 상태를 그대로 알린다
  const scopeErr = mineScope ? universe.error : latest.error;
  const noSnapshot = scopeErr instanceof ApiError && scopeErr.status === 404;
  // 기업 중심 그래프만 실패한 경우(딥링크한 기업이 스냅샷에 없음 등) — 씬이 빈 이유를 말하고 은하로 돌아갈 길을 준다
  const focusError = Boolean(focusId) && focusGraph.isError && !systemModel;
  const focusNoSnapshot = focusGraph.error instanceof ApiError && focusGraph.error.status === 404;
  // 관심 기업이 0개거나(watchIds 없음), 있어도 전부 우주 스냅샷 밖이라 배치할 별이 없으면 씬 대신 안내 카드를 보여 준다
  // (워프해 들어간 상태에서는 씬이 주인공이므로 비운다)
  const noStars = watchIds.length === 0 || (mine !== null && mine.model.nodes.length === 0);
  const showEmpty = mineScope && status === "authed" && watchlist.data !== undefined && !focusId && noStars;

  /* 산업 선택판 — 넓은 화면은 상단 한 줄의 마지막 항목, 모바일은 둘째 줄에서 Special Link 꼬리표와 나란히 */
  const industryPicker = (
    <IndustryPicker
      industries={industries.data?.items ?? []}
      loading={industries.isLoading}
      total={universe.data?.nodes.length}
      value={industryId}
      onChange={(id) => {
        setIndustry(id);
        if (focusId) warpTo(null);
      }}
    />
  );
  return (
    <div
      className={`galaxy${panelActive ? " hud-panel-active" : ""}`}
      onPointerMove={(e) => {
        pointer.current.x = e.clientX;
        pointer.current.y = e.clientY;
      }}
    >
      <div className="stage">
        <Suspense fallback={null}>
          <Scene galaxy={galaxyModel} system={systemModel} next={nextModel} />
        </Suspense>
      </div>
      <HoverPrefetch focusId={focusId} />
      {/* 빔·기업 호버 툴팁 — 포인터를 따라가는 DOM, 클릭은 통과시킨다 */}
      <SceneTip model={focusId ? systemModel : galaxyModel} pointer={pointer} />
      {/* 모바일 길게 누르기 진행 원 — 손가락 자리에 차오르고 다 차면 관계망으로 */}
      <HoldRing />
      {/* 첫 방문 4단 스포트라이트 투어 (은하 뷰 전용) 와 `?` 도움말 허브 — 문구는 둘 다 lib/guide 한 곳 */}
      <Tour model={galaxyModel} autoStart={autoTour} />
      <HelpHotkey />
      {helpOpen && <HelpHub />}

      {/* 로딩 — 언마운트하지 않고 .off 로 접어 0.35s 페이드아웃한다: index.html 의 정적 로더 → 이 오버레이 → 씬 순으로 끊김 없이 이어진다 */}
      <div className={`stage-loading${showLoading ? "" : " off"}`} aria-live="polite" aria-hidden={!showLoading}>
        <span className="kick">news × relationship graph</span>
        <b>COSMOS</b>
        <i />
        <span>{!showLoading ? "" : focusId ? "기업 관계망을 불러오는 중" : mineScope ? "내 은하를 불러오는 중" : "최신 스냅샷을 불러오는 중"}</span>
      </div>
      {scopeError && (
        <div className="stage-loading" style={{ pointerEvents: "auto" }}>
          <b>{noSnapshot ? "아직 공개된 관계 그래프가 없습니다" : "연결 실패"}</b>
          <span>{noSnapshot ? "뉴스·공시 분석 결과가 게시되면 은하가 나타납니다. 뉴스·기업 탭은 지금도 볼 수 있습니다." : "그래프 스냅샷을 가져오지 못했습니다."}</span>
          <button type="button" className="btn btn-g btn-sm" onClick={() => (mineScope ? universe.refetch() : latest.refetch())}>
            다시 시도
          </button>
        </div>
      )}
      {!scopeError && focusError && (
        <div className="stage-loading" style={{ pointerEvents: "auto" }}>
          <b>{focusNoSnapshot ? "이 기업의 관계망이 아직 없습니다" : "관계망을 불러오지 못했습니다"}</b>
          <span>{focusNoSnapshot ? "공개된 스냅샷에 이 기업의 관계가 없습니다. 분석 결과가 게시되면 나타납니다." : "기업 관계 그래프를 가져오지 못했습니다."}</span>
          <div className="row" style={{ gap: 8 }}>
            <button type="button" className="btn btn-g btn-sm" onClick={() => focusGraph.refetch()}>
              다시 시도
            </button>
            <button type="button" className="btn btn-g btn-sm" onClick={() => warpTo(null)}>
              은하로 돌아가기
            </button>
          </div>
        </div>
      )}
      {showEmpty && <MyGalaxyEmpty outOfSnapshot={watchIds.length > 0} onSearch={() => setSearchOpen(true)} onBack={() => pickScope("all")} />}

      {/* 상단 한 줄: 검색 · 은하 범위 · 산업 선택판 (은하 뷰에서만) */}
      {!focusId && (
      <div className="hud hud-top">
        {/* 모바일에서는 글자를 감추고 돋보기 아이콘만 남는다(galaxy.css) — 라벨은 aria-label 로 유지 */}
        <button type="button" className="field search-trigger" onClick={() => setSearchOpen(true)} aria-label="기업 검색" title="기업명 또는 종목코드 검색">
          <Icon.Search />
          <span>기업명 또는 종목코드를 검색하세요</span>
        </button>
        {/* 카메라 시점 — 둘러보기 / 거리 비교 / 고도 비교 */}
        <CameraPresets />
        {/* 은하 범위 — 탭을 늘리지 않고 같은 화면을 전체 우주 / 내 관심 기업 두 가지로 쓴다 */}
        <div className="seg scope-seg" role="group" aria-label="은하 범위">
          <button type="button" className={mineScope ? "" : "on"} aria-pressed={!mineScope} onClick={() => pickScope("all")}>
            전체 은하
          </button>
          <button type="button" className={mineScope ? "on" : ""} aria-pressed={mineScope} onClick={() => pickScope("mine")} title={status === "authed" ? "관심 기업과 그 1홉 관계만 보기" : "로그인하면 관심 기업으로 개인 은하를 만들 수 있습니다"}>
            <Icon.Star filled={mineScope} /> 내 은하
            {status === "authed" && <b className="num">{watchIds.length}</b>}
          </button>
        </div>
        {mineScope ? (
          <MyGalaxyBar
            watchCount={watchIds.length}
            neighborCount={mine?.neighborIds.length ?? 0}
            watchedIds={watchIds}
            onAdd={addToMyGalaxy}
            missing={(mine?.missing ?? []).map((id) => ({ companyId: id, name: watchItems.find((w) => w.companyId === id)?.name ?? id }))}
            onPick={pickCompany}
          />
        ) : (
          industryPicker
        )}
      </div>
      )}

      {/* 좌상단: 뉴스 페이지에서 관계선 보기로 들어온 경우 되돌아가는 길 — 은하 뷰(focusId 없음)에서만 의미가 있다.
          예전엔 다른 브레드크럼과 같은 옅은 알약 버튼이라 눈에 잘 안 띈다는 피드백을 받았다 — 사용자가 X 로 직접
          닫기 전까지는 화면에 계속 남아 있는, 더 도드라진 팝업 카드로 바꿨다(자동으로 사라지지 않는다) */}
      {!focusId && newsReturnTo && !newsReturnDismissed && (
        // 은하 뷰(비 focus)에서는 hud-top 검색창 줄이 이미 top:22px 를 쓰고 있어 hud-crumb 기본
        // 위치(top:22px)와 겹친다 — 검색창 줄 아래로 내리는 전용 modifier 를 쓴다(galaxy.css).
        <div className="hud hud-crumb hud-crumb-below-search">
          <div className="news-return-toast" role="status">
            <button type="button" className="news-return-go" onClick={() => navigate(newsReturnTo)} title="보고 있던 뉴스로 돌아가기">
              <Icon.Back /> 뉴스로 돌아가기
            </button>
            <button type="button" className="news-return-close" onClick={() => setNewsReturnDismissed(true)} aria-label="이 안내 닫기" title="닫기">
              <Icon.Close />
            </button>
          </div>
        </div>
      )}
      {/* 좌상단: 기업 중심 뷰 브레드크럼 */}
      {focusId && (
        <div className="hud hud-crumb">
          <div className="crumb">
            <button type="button" onClick={() => warpTo(null)} title="은하로 돌아가기 (ESC)">
              <Icon.Back /> {mineScope ? "내 은하" : "전체 은하"}
            </button>
            <span aria-hidden="true">›</span>
            <b>{focusName}</b>
            <span className="meta">중심 관계망 · 최대 3단계</span>
          </div>
          <button type="button" className="crumb" onClick={() => setSearchOpen(true)} title="다른 기업 검색">
            <Icon.Search />
          </button>
          <CameraPresets />
        </div>
      )}
      {/* 브레드크럼 아래: 중심 기업의 1홉 역할 요약 (칩을 누르면 그 역할만 남긴다) */}
      <HopSummary model={systemModel} />
      {/* 상단 중앙: 최단 경로 스트립 — 기업 중심 뷰는 중심 기준, 은하 뷰는 미리보기로 고른 기업 기준 (자기 모델로 재계산, Canvas 와 결합도 0) */}
      <PathStrip model={focusId ? systemModel : galaxyModel} />

      {/* 좌하단: 상태 */}
      <div className="hud hud-status">
        <div className="card status-card">
          <div>
            <div className="t">
              <i className={`dot ${stale ? "warn" : ""}`} />
              {mineScope ? `내 은하 · 관심 ${watchIds.length}` : snapshot?.personalized ? "개인화 그래프" : "공개 스냅샷"}
              {stale && <span className="meta">· 새 스냅샷 가능</span>}
            </div>
            <div className="meta num" style={{ marginTop: 4 }}>
              {snapshot
                ? `${fmtDateTime(snapshot.asOfAt)} 기준 · 다음 갱신 ${fmtDateTime(snapshot.nextRefreshAt).slice(-5)}`
                : scopeError
                  ? noSnapshot
                    ? "공개된 스냅샷 없음 · 분석 결과 게시 전"
                    : "스냅샷 확인 실패"
                  : "불러오는 중"}
            </div>
          </div>
        </div>
      </div>

      {/* 왼쪽: 조절판(구 하단 중앙 도크) — Special Link 와 자리를 맞바꿔 세로로 길게 선다. 알약 버튼으로 열면
          펼치기 슬라이더(은하 → 평면 → 크기순 정렬) · 관계 종류 4개 · 고급(뉴스·공시 비율 · 최소 점수 · 주가 고도) 순으로 쌓인다.
          씬 인코딩 설명(자리·굵기·색·점선)은 ? 허브에만 둔다 */}
      <ControlDock />
      {/* 하단 중앙: Special Link(구 조절판 자리) — 특별한 관계만 골라 보여 주는 이 제품의 핵심 카드. 기본은 접힌 꼬리표이고,
          펼쳐도 가로 한 줄(가로 스크롤)로만 보여 화면을 많이 가리지 않는다. 행 호버 = 씬 강조, 클릭 = 근거 패널 */}
      <SurpriseRail model={focusId ? systemModel : galaxyModel} onPickCompany={pickCompany} />

      {/* 우측 패널 — 닫으면 오른쪽 가장자리 꼬리표로 접힌다 */}
      {/* 은하 뷰에서 행성을 클릭하면 워프하지 않고 그 기업의 뉴스·연결 단계를 여기서 먼저 본다 — 더블클릭(또는 패널의 "관계망 보기")이 워프 */}
      {(focusId || selectedEdgeId || selectedId) && !panelOpen && (
        <div className="hud hud-panel-tag">
          <button type="button" className="hud-tag vertical" onClick={() => setPanelOpen(true)} title="패널 펼치기">
            {selectedEdgeId ? "기업 관계" : `${focusName ?? galaxyModel?.nodeById.get(selectedId ?? "")?.name ?? ""} · 인텔리전스`}
          </button>
        </div>
      )}
      {panelActive && <div className="hud hud-panel">{panelContent}</div>}

      <SearchPalette onPick={pickFromSearch} />
    </div>
  );
}
