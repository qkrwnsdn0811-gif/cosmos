import { create } from "zustand";
import type { Horizon } from "@/api/types";

const HORIZON_KEY = "cosmos.invest-horizon";

function readHorizon(): Horizon {
  try {
    return localStorage.getItem(HORIZON_KEY) === "LONG" ? "LONG" : "SHORT";
  } catch {
    return "SHORT";
  }
}

/**
 * 투자자 유형 토글(F9) — 단기(3거래일)·장기(20거래일). 뉴스 피드와 뉴스 상세의 딱지·과거 사례 기간·지표 순서가 함께 바뀐다.
 * 고른 값은 이 브라우저에만 기억한다 (저장이 막힌 환경이면 매번 단기로 시작)
 */
export const useInvest = create<{ horizon: Horizon; setHorizon(h: Horizon): void }>((set) => ({
  horizon: readHorizon(),
  setHorizon: (horizon) => {
    try {
      localStorage.setItem(HORIZON_KEY, horizon);
    } catch {
      /* 저장 불가 — 이번 방문에만 유지 */
    }
    set({ horizon });
  },
}));
