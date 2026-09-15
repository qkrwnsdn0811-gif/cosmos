import { create } from "zustand";
import type { TourStepId } from "@/lib/guide";

export type AuthDialog = "login" | "signup" | null;
/** 다이얼로그를 열 때 넘기는 초기값 — 가입 후 로그인 전환처럼 문맥을 이어줄 때 쓴다 */
export interface AuthPrefill {
  email?: string;
  notice?: string;
}
export interface Toast {
  id: number;
  tone: "info" | "success" | "error";
  message: string;
}

interface UiState {
  authDialog: AuthDialog;
  authPrefill: AuthPrefill | null;
  toasts: Toast[];
  /** `?` 도움말 허브 — 비모달, 씬은 뒤에서 계속 움직인다 */
  helpOpen: boolean;
  /** 열 때 스크롤할 절 id (lib/guide HELP_SECTIONS) */
  helpSection: string | null;
  /** 첫 방문 투어 재생 요청 — 허브의 "투어 다시 보기"·"화면에서 보기". nonce 가 바뀔 때마다 Tour 가 그 단계에서 시작한다 */
  tourRequest: { nonce: number; step: TourStepId | null };
  openAuth(kind: Exclude<AuthDialog, null>, opts?: AuthPrefill): void;
  closeAuth(): void;
  toast(message: string, tone?: Toast["tone"]): void;
  dismissToast(id: number): void;
  openHelp(section?: string): void;
  closeHelp(): void;
  toggleHelp(): void;
  requestTour(step?: TourStepId): void;
}

let seq = 0;
export const useUi = create<UiState>((set) => ({
  authDialog: null,
  authPrefill: null,
  toasts: [],
  helpOpen: false,
  helpSection: null,
  tourRequest: { nonce: 0, step: null },
  openAuth(kind, opts) {
    // opts 를 주지 않은 기존 호출은 프리필 없이 여는 동작 그대로다
    set({ authDialog: kind, authPrefill: opts ?? null });
  },
  closeAuth() {
    set({ authDialog: null, authPrefill: null });
  },
  toast(message, tone = "info") {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts, { id, tone, message }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 3600);
  },
  dismissToast(id) {
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
  },
  openHelp(section) {
    set({ helpOpen: true, helpSection: section ?? null });
  },
  closeHelp() {
    set({ helpOpen: false, helpSection: null });
  },
  toggleHelp() {
    set((s) => ({ helpOpen: !s.helpOpen, helpSection: null }));
  },
  requestTour(step) {
    set((s) => ({ tourRequest: { nonce: s.tourRequest.nonce + 1, step: step ?? null } }));
  },
}));
