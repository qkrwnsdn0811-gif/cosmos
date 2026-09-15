import { create } from 'zustand';
import { N_DAYS, REL_TYPES } from '../data/universe';

/**
 * 화면 전체의 단일 상태.
 *
 * phase 흐름:
 *   intro ──(탐사 시작)──> galaxy ──(노드 클릭)──> warp ──> system
 *                              ^                                │
 *                              └──────(은하로 복귀 / warp)───────┘
 */
export const useGalaxy = create((set, get) => ({
  phase: 'intro', // intro | galaxy | warp | system
  warpStartedAt: 0,
  warpTarget: null, // 워프가 끝나면 focus 가 될 기업 id (null = 은하 복귀)

  focus: null, // system 뷰에서 중심에 있는 기업 id
  hovered: null, // 호버 중인 기업 id
  selectedEdge: null, // 선택된 관계 key ("aId|bId")

  day: 248, // 현재 시점 (영업일 인덱스) — AI 인프라 내러티브가 살아있는 구간에서 시작
  playing: false,
  threshold: 0.42, // 간선 표시 최소 |상관계수|
  activeTypes: new Set(Object.keys(REL_TYPES)),
  onlySerendipity: false,
  showLabels: true,

  /* ---------------- 전환 ---------------- */
  begin: () => set({ phase: 'galaxy' }),

  warpTo: (companyId) => {
    const s = get();
    if (s.phase === 'warp' || s.phase === 'intro') return;
    if (companyId && companyId === s.focus) return;
    set({
      phase: 'warp',
      warpStartedAt: performance.now(),
      warpTarget: companyId,
      warpCommitted: false,
      selectedEdge: null,
      hovered: null,
      playing: false,
    });
  },

  // 워프 중간 지점: 터널이 화면을 가린 사이 씬을 교체한다
  warpCommitted: false,
  commitWarp: () => set((s) => ({ warpCommitted: true, focus: s.warpTarget })),

  // 워프 종료
  completeWarp: () => set((s) => ({ phase: s.warpTarget ? 'system' : 'galaxy' })),

  backToGalaxy: () => get().warpTo(null),

  /* ---------------- 상호작용 ---------------- */
  setHovered: (id) => set({ hovered: id }),
  selectEdge: (key) => set((s) => ({ selectedEdge: s.selectedEdge === key ? null : key })),
  clearSelection: () => set({ selectedEdge: null }),

  /* ---------------- 타임라인 ---------------- */
  setDay: (d) => set({ day: Math.max(0, Math.min(N_DAYS - 1, Math.round(d))) }),
  stepDay: (delta) =>
    set((s) => ({ day: Math.max(0, Math.min(N_DAYS - 1, s.day + delta)) })),
  togglePlay: () => set((s) => ({ playing: !s.playing })),
  setPlaying: (v) => set({ playing: v }),

  /* ---------------- 필터 ---------------- */
  setThreshold: (v) => set({ threshold: v }),
  toggleType: (t) =>
    set((s) => {
      const next = new Set(s.activeTypes);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return { activeTypes: next };
    }),
  toggleSerendipity: () => set((s) => ({ onlySerendipity: !s.onlySerendipity })),
  toggleLabels: () => set((s) => ({ showLabels: !s.showLabels })),
}));
