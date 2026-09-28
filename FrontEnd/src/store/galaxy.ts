import { create } from "zustand";
import type { AltitudeWindow, MetricWindow, PricePeriod, RelationshipType } from "@/api/types";
import type { CameraPresetId } from "@/lib/guide";
import { RELATIONSHIP_ORDER } from "@/lib/meta";
import type { RoleKey } from "@/lib/roles";

/** 모바일에서 행성을 이만큼 누르면 관계망으로 워프한다 — HoldRing 의 채움 애니메이션 길이와 같은 값 */
export const HOLD_MS = 550;

export type PanelTab = "news" | "price" | "board";
/** 은하 범위 — "all" 전체 우주, "mine" 관심 기업(+1홉)만 보는 개인 은하 */
export type GalaxyScope = "all" | "mine";
export type Phase = "galaxy" | "warp" | "system";

interface GalaxyState {
  /* ---- 뷰 상태 ---- */
  phase: Phase;
  focusId: string | null; // system 뷰의 중심 기업
  warpTarget: string | null; // 워프가 끝나면 focus 가 될 기업 (null = 은하 복귀)
  warpStartedAt: number;
  warpCommitted: boolean;

  hoveredId: string | null;
  hoveredEdgeId: string | null;
  /** 모바일 길게 누르기(관계망으로) 진행 표시 — 누른 자리(뷰포트 px)와 기업. CompanyNodes 가 세우고, 손을 떼거나 끌기 시작하면 지운다 */
  hold: { id: string; x: number; y: number } | null;
  selectedEdgeId: string | null;
  /**
   * 미리보기로 고른 기업 (은하 뷰에서 행성 클릭). 우측 패널에 뉴스·연결 단계가 뜨고, 다른 기업을 호버하면 여기서 가는 경로가 강조된다.
   * 더블클릭(warpTo)하면 그 기업의 관계망으로 들어가며 이 값은 비워진다. 파생값(경로)은 스토어에 두지 않는다
   */
  selectedId: string | null;
  /** 기업 중심 뷰 1홉 요약에서 고른 역할 — 그 역할의 이웃·간선만 남긴다 */
  roleFilter: RoleKey | null;

  /* ---- 필터 ---- */
  /** 은하에 무엇을 올릴지 — 전체 우주냐 내 관심 기업이냐. 같은 화면을 두 범위로 쓴다 */
  scope: GalaxyScope;
  industryId: string | null;
  minScore: number;
  activeTypes: Set<RelationshipType>;
  showLabels: boolean;
  /** 간선 예산: 켜면 점수 상위 관계만 기본 표시, 나머지는 노드 호버 때만 */
  edgeBudget: boolean;
  /** 주가 고도: 켜면 은하 뷰의 행성이 기간 등락률만큼 원반 위·아래로 뜬다 (기업 중심 뷰에는 적용하지 않는다) */
  altitudeOn: boolean;
  /** 고도의 기준 기간 */
  altitudeWindow: AltitudeWindow;
  /**
   * 관계선(간선) 자체를 계산하는 기준 기간 — ControlDock 의 주가 고도 옆 새 버튼이 고른다.
   * 이 값이 바뀌면 lib/queries 의 useLatestGraph·useCompanyGraph 가 백엔드에 다른 window 로 다시 물어 edges 가
   * 통째로 달라진다(관계 유무·점수가 기간마다 다르게 나올 수 있다). CompanyPanel/RelationshipPanel 의 `window`
   * (이미 고른 관계 하나의 지표를 보는 기간), altitudeWindow(주가 고도)와는 값 체계만 같고 완전히 별개 상태다.
   * 메인 은하·기업 중심 뷰·관계 경로(PathGalaxyPage)·관계 검색 추천(SearchPalette)이 모두 이 값을 함께 쓴다.
   */
  edgeWindow: MetricWindow;
  /**
   * 펼치기 0~1 — 0 나선 은하 · 0.5 평면 · 1 행성 크기순 격자 (lib/spread). 은하 뷰 전용이라 워프하면 0 으로 돌아간다.
   * 서버·저장소에 두지 않는 이 화면의 표시 설정이다
   */
  spread: number;

  /* ---- 패널 ---- */
  panelTab: PanelTab;
  window: MetricWindow;
  pricePeriod: PricePeriod;
  searchOpen: boolean;
  /** 우측 인텔리전스 패널 펼침 여부 (접으면 오른쪽 가장자리에 꼬리표) */
  panelOpen: boolean;
  /** 하단 중앙 조절 도크(펼치기·관계 종류·뉴스·공시 비율·최소 점수·주가 고도) 펼침 여부 — '^' 버튼으로 연다.
      고급 영역(뉴스·공시 비율·최소 점수·주가 고도)은 예전엔 이 안에서 다시 '>' 로 접고 펼 수 있었지만,
      항상 켜 있어야 한다는 결정에 따라 그 토글(dockAdvanced)은 없앴다 — 도크가 열리면 고급 영역도 항상 같이 보인다 */
  dockOpen: boolean;
  /** 상단 산업 선택판 펼침 여부 — ESC 한 겹으로 닫히도록 스토어에 둔다 */
  industryOpen: boolean;
  /** 선 중간의 관계 종류 글자(EdgeLabels) 표시 여부 */
  showEdgeLabels: boolean;
  /** 왼쪽 "Special Link" 카드 펼침 여부 (접으면 꼬리표) */
  surpriseOpen: boolean;
  /** 카메라 프리셋 — 궤도(둘러보기) · 위에서(거리 비교) · 정면에서(고도 비교). Director 가 자세를 만든다 */
  cameraPreset: CameraPresetId;
  /** 프리셋을 고른 횟수 — 같은 프리셋을 다시 눌러도 카메라가 그 자세로 돌아가도록 값이 아니라 횟수로 알린다 */
  cameraPresetNonce: number;

  /* ---- 액션 ---- */
  warpTo(companyId: string | null): void;
  commitWarp(): void;
  completeWarp(): void;
  backToGalaxy(): void;
  setHovered(id: string | null): void;
  setHold(h: { id: string; x: number; y: number } | null): void;
  setHoveredEdge(id: string | null): void;
  selectEdge(id: string | null): void;
  clearSelection(): void;
  selectCompany(id: string | null): void;
  setRoleFilter(r: RoleKey | null): void;
  setScope(scope: GalaxyScope): void;
  setIndustry(id: string | null): void;
  setMinScore(v: number): void;
  toggleType(t: RelationshipType): void;
  toggleLabels(): void;
  toggleEdgeBudget(): void;
  setAltitude(on: boolean): void;
  setAltitudeWindow(w: AltitudeWindow): void;
  setEdgeWindow(w: MetricWindow): void;
  setSpread(v: number): void;
  setDockOpen(v: boolean): void;
  /** 도크가 다루는 표시 설정을 기본값으로 — 펼치기 0 · 관계 종류 전부 · 최소 점수 0 · 주가 고도 켬(1M) · 관계선 기준 기간 1M.
      뉴스·공시 비율은 store/weight 가 따로 되돌린다 */
  resetControls(): void;
  setPanelTab(t: PanelTab): void;
  setWindow(w: MetricWindow): void;
  setPricePeriod(p: PricePeriod): void;
  setSearchOpen(v: boolean): void;
  setPanelOpen(v: boolean): void;
  setIndustryOpen(v: boolean): void;
  setCameraPreset(p: CameraPresetId): void;
  toggleEdgeLabels(): void;
  toggleSurprise(): void;
}

export const useGalaxy = create<GalaxyState>((set, get) => ({
  phase: "galaxy",
  focusId: null,
  warpTarget: null,
  warpStartedAt: 0,
  warpCommitted: false,

  hoveredId: null,
  hoveredEdgeId: null,
  hold: null,
  selectedEdgeId: null,
  selectedId: null,
  roleFilter: null,

  scope: "all",
  industryId: null,
  minScore: 0,
  activeTypes: new Set(RELATIONSHIP_ORDER),
  showLabels: true,
  edgeBudget: true,
  altitudeOn: true,
  altitudeWindow: "30D",
  edgeWindow: "30D",
  spread: 0,

  panelTab: "news",
  window: "30D",
  pricePeriod: "1M",
  searchOpen: false,
  panelOpen: true,
  // 조절판(도크)은 펼친 채로 시작한다 — 조절 수단(관계 종류·뉴스·공시 비율·최소 점수·주가 고도)이 첫 화면에 모두 보여야 한다.
  // 접으면 왼쪽 알약 버튼만 남는다. 열리면 고급 영역까지 항상 함께 보인다(예전의 '>' 토글은 없앴다)
  dockOpen: true,
  industryOpen: false,
  cameraPreset: "orbit",
  cameraPresetNonce: 0,
  showEdgeLabels: true,
  // Special Link 는 접힌 채로 시작한다 — 하단 중앙 꼬리표 하나만 보이고, 사용자가 직접 펼쳐야 카드가 뜬다
  surpriseOpen: false,

  warpTo(companyId) {
    const s = get();
    if (s.phase === "warp") return;
    if (companyId !== null && companyId === s.focusId) return;
    if (companyId === null && s.focusId === null) return;
    set({
      phase: "warp",
      warpStartedAt: performance.now(),
      warpTarget: companyId,
      warpCommitted: false,
      selectedEdgeId: null,
      hoveredId: null,
      hoveredEdgeId: null,
      selectedId: null,
      roleFilter: null,
      panelOpen: true,
      industryOpen: false,
      // 펼치기는 은하 뷰의 배치다 — 기업 중심 뷰로 들어가면(또는 돌아오면) 나선 배치에서 시작한다
      spread: 0,
    });
  },
  commitWarp() {
    set((s) => ({ warpCommitted: true, focusId: s.warpTarget }));
  },
  completeWarp() {
    set((s) => ({ phase: s.warpTarget ? "system" : "galaxy" }));
  },
  backToGalaxy() {
    get().warpTo(null);
  },

  setHovered(id) {
    if (get().hoveredId !== id) set({ hoveredId: id });
  },
  setHold(h) {
    set({ hold: h });
  },
  setHoveredEdge(id) {
    if (get().hoveredEdgeId !== id) set({ hoveredEdgeId: id });
  },
  selectEdge(id) {
    // 간선을 고르면 기업 미리보기는 내려간다 — 우측 패널은 한 번에 하나
    set((s) => {
      const opening = id !== null && s.selectedEdgeId !== id;
      return {
        selectedEdgeId: s.selectedEdgeId === id ? null : id,
        selectedId: null,
        panelOpen: true,
        // 관계선을 새로 고르면 RelationshipPanel 의 표시 기간(window)을 그 관계선을 만든 기준 기간(edgeWindow)으로
        // 맞춘다 — 다른 기간에는 이 관계 자체가 없을 수 있어(예: 10Y 기준에서만 보이는 관계) 상세 조회가 404 날 수 있다.
        // 패널을 연 뒤에는 사용자가 그 안의 기간 버튼으로 자유롭게 다른 기간을 둘러볼 수 있다
        ...(opening ? { window: s.edgeWindow } : null),
      };
    });
  },
  clearSelection() {
    set({ selectedEdgeId: null, selectedId: null, hoveredId: null, hoveredEdgeId: null, roleFilter: null });
  },
  selectCompany(id) {
    // 워프 중에는 아직 이전 모델이 보이고 집을 수도 있다 — 그때 고른 기업은 착지한 뷰에 없을 수 있다 (warpTo 와 같은 가드)
    if (get().phase === "warp") return;
    set({ selectedId: id, selectedEdgeId: null, panelOpen: true });
  },
  setRoleFilter(r) {
    set((s) => ({ roleFilter: s.roleFilter === r ? null : r }));
  },

  setScope(scope) {
    // 범위가 바뀌면 모델이 통째로 갈린다 — 새 모델에 없을 수 있는 상태(선택 간선·경로 핀·역할 필터)를 setIndustry 와 같게 턴다
    set({ scope, selectedEdgeId: null, selectedId: null, roleFilter: null, industryOpen: false });
  },
  setIndustry(id) {
    // 새 모델에는 없을 수 있는 상태(선택 간선·경로 핀·역할 필터)를 함께 턴다 — 핀이 남으면 crosshair 커서만 남고 경로가 잡히지 않는다
    set({ industryId: id, selectedEdgeId: null, selectedId: null, roleFilter: null });
  },
  setMinScore(v) {
    set({ minScore: Math.max(0, Math.min(100, v)) });
  },
  toggleType(t) {
    set((s) => {
      const next = new Set(s.activeTypes);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return { activeTypes: next };
    });
  },
  toggleEdgeBudget() {
    set((s) => ({ edgeBudget: !s.edgeBudget }));
  },
  setAltitude(on) {
    set({ altitudeOn: on });
  },
  setAltitudeWindow(w) {
    set({ altitudeWindow: w });
  },
  setEdgeWindow(w) {
    set({ edgeWindow: w });
  },
  setSpread(v) {
    set({ spread: Math.max(0, Math.min(1, v)) });
  },
  setDockOpen(v) {
    set({ dockOpen: v });
  },
  resetControls() {
    set({ spread: 0, activeTypes: new Set(RELATIONSHIP_ORDER), minScore: 0, altitudeOn: true, altitudeWindow: "30D", edgeWindow: "30D" });
  },
  toggleLabels() {
    set((s) => ({ showLabels: !s.showLabels }));
  },
  setPanelTab(t) {
    set({ panelTab: t });
  },
  setWindow(w) {
    set({ window: w });
  },
  setPricePeriod(p) {
    set({ pricePeriod: p });
  },
  setSearchOpen(v) {
    set({ searchOpen: v });
  },
  setPanelOpen(v) {
    set({ panelOpen: v });
  },
  setIndustryOpen(v) {
    set({ industryOpen: v });
  },
  setCameraPreset(p) {
    set((s) => ({ cameraPreset: p, cameraPresetNonce: s.cameraPresetNonce + 1 }));
  },
  toggleEdgeLabels() {
    set((s) => ({ showEdgeLabels: !s.showEdgeLabels }));
  },
  toggleSurprise() {
    set((s) => ({ surpriseOpen: !s.surpriseOpen }));
  },
}));
