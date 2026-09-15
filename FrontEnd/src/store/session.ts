import { create } from "zustand";
import { api, ApiError, type Me } from "@/api";
import { setSessionExpiredHandler, tokenStore } from "@/api/client";
import { useUi } from "@/store/ui";

type Status = "booting" | "guest" | "authed";

/**
 * 로그인한 적이 있다는 힌트. Access Token 은 메모리에만 있으므로 새로고침 후
 * refresh 를 시도할지 판단할 근거가 필요하다. 힌트가 없으면 게스트로 바로 확정해
 * 첫 화면에서 불필요한 /auth/refresh 왕복과 쿼리 두 번 호출을 없앤다.
 */
const HINT_KEY = "cosmos.session.hint";
function hasHint() {
  try {
    return localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}
function setHint() {
  try {
    localStorage.setItem(HINT_KEY, "1");
  } catch {
    /* 프라이빗 모드 등 저장 실패는 무시한다 */
  }
}
function clearHint() {
  try {
    localStorage.removeItem(HINT_KEY);
  } catch {
    /* ignore */
  }
}

interface SessionState {
  status: Status;
  user: Me | null;
  /** 로그인 사용자별 캐시 분리를 위한 키 */
  sessionKey: string;
  bootstrap(): Promise<void>;
  login(email: string, password: string): Promise<void>;
  /** 가입은 성공했지만 자동 로그인(login/me)이 실패할 수 있어 결과를 알려 준다 */
  signup(email: string, password: string, nickname: string): Promise<{ autoLogin: boolean }>;
  logout(): Promise<void>;
  setUser(user: Me): void;
}

const GUEST = { status: "guest" as Status, user: null, sessionKey: "guest" };

// StrictMode 는 마운트 effect 를 두 번 연달아 실행한다 — 진행 중인 bootstrap 을
// 그대로 재사용해 /auth/refresh, /users/me 중복 호출과 상태 덮어쓰기 경합을 막는다
let bootPromise: Promise<void> | null = null;

export const useSession = create<SessionState>((set, get) => ({
  // 힌트가 없으면 기다릴 이유가 없다 — 게스트는 첫 렌더부터 게스트다
  status: hasHint() ? "booting" : "guest",
  user: null,
  sessionKey: "guest",

  bootstrap() {
    if (bootPromise) return bootPromise;
    setSessionExpiredHandler(() => {
      clearHint();
      set({ ...GUEST });
      useUi.getState().toast("세션이 만료되었습니다. 다시 로그인해 주세요.", "error");
    });
    if (get().status === "guest") return Promise.resolve();
    // userId 는 64비트 정수라 캐시 키로 쓰려면 문자열로 바꾼다
    bootPromise = (async () => {
      try {
        await api.auth.refresh();
        const me = await api.users.me();
        setHint();
        set({ status: "authed", user: me, sessionKey: String(me.userId) });
      } catch {
        tokenStore.set(null);
        clearHint();
        set({ ...GUEST });
      }
    })().finally(() => {
      bootPromise = null;
    });
    return bootPromise;
  },

  async login(email, password) {
    await api.auth.login({ email, password });
    const me = await api.users.me();
    setHint();
    set({ status: "authed", user: me, sessionKey: String(me.userId) });
  },

  async signup(email, password, nickname) {
    await api.auth.signup({ email, password, nickname });
    // 백엔드에 로그인 엔드포인트가 아직 없을 수 있다. 실패해도 가입 자체는 성공이므로 삼킨다
    try {
      await api.auth.login({ email, password });
      const me = await api.users.me();
      setHint();
      set({ status: "authed", user: me, sessionKey: String(me.userId) });
      return { autoLogin: true };
    } catch {
      tokenStore.set(null);
      clearHint();
      return { autoLogin: false };
    }
  },

  async logout() {
    try {
      await api.auth.logout();
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    }
    clearHint();
    tokenStore.set(null);
    set({ ...GUEST });
  },

  setUser(user) {
    set({ user });
  },
}));
