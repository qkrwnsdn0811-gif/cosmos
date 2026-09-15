import { create } from "zustand";
import { DEFAULT_NEWS_WEIGHT } from "@/lib/score";

/**
 * 뉴스·공시 표시 비율 — 관계 점수를 화면에서 어떤 비율로 섞어 볼지 정한다.
 *
 * 서버에 저장하지 않는다 (명세 graph '관계 점수 표시 규칙'). 같은 탭의 페이지 이동·새로고침에서는
 * sessionStorage 로 유지되고, 탭 세션이 끝난 뒤 다시 들어오면 기본 50:50 으로 돌아간다.
 * 로그인과 무관한 화면 설정이라 세션 스토어(store/session)와 섞지 않고 따로 둔다.
 */
const KEY = "cosmos.weight.news";
// 기본값(0~1)은 점수 규칙과 같은 곳에서 온다 — 공시 가중치는 언제나 1 - newsWeight 다
export { DEFAULT_NEWS_WEIGHT };

const clamp = (v: number) => Math.min(1, Math.max(0, v));

function load() {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (raw === null) return DEFAULT_NEWS_WEIGHT;
    const v = Number(raw);
    return Number.isFinite(v) ? clamp(v) : DEFAULT_NEWS_WEIGHT;
  } catch {
    // 프라이빗 모드 등 저장소 접근 실패 — 이번 탭에서는 기본값으로 동작한다
    return DEFAULT_NEWS_WEIGHT;
  }
}
function save(v: number) {
  try {
    if (v === DEFAULT_NEWS_WEIGHT) sessionStorage.removeItem(KEY);
    else sessionStorage.setItem(KEY, String(v));
  } catch {
    /* ignore */
  }
}

interface WeightState {
  /** 0~1. 화면(슬라이더)은 퍼센트로 보여 주고 계산은 이 값을 쓴다 */
  newsWeight: number;
  setNewsWeight(v: number): void;
  reset(): void;
}

export const useWeight = create<WeightState>((set) => ({
  newsWeight: load(),
  setNewsWeight(v) {
    const next = clamp(v);
    save(next);
    set({ newsWeight: next });
  },
  reset() {
    save(DEFAULT_NEWS_WEIGHT);
    set({ newsWeight: DEFAULT_NEWS_WEIGHT });
  },
}));

/** 점수 계산에 쓰는 가중치만 구독 — 값이 바뀔 때만 다시 계산된다 */
export const useNewsWeight = () => useWeight((s) => s.newsWeight);
/** 슬라이더·설명에 쓰는 정수 퍼센트 (뉴스 %) */
export const pct = (newsWeight: number) => Math.round(newsWeight * 100);
