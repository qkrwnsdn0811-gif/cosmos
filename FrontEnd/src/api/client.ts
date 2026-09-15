import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  fieldErrors: ApiErrorBody["fieldErrors"];
  constructor(status: number, body: Partial<ApiErrorBody> | null) {
    super(body?.message ?? `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "UNKNOWN";
    this.fieldErrors = body?.fieldErrors ?? [];
  }
}

/** Access Token 은 메모리에만 둔다. 새로고침 시 refresh 쿠키로 재발급한다. */
let accessToken: string | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
/** 만료 60초 전에 미리 갱신한다 — 사용자가 401 을 마주치기 전에 토큰을 바꿔 끼운다 */
const REFRESH_LEAD_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 5_000;
/** setTimeout 은 32비트 지연을 넘기면 즉시 실행되므로 상한을 둔다 */
const MAX_REFRESH_DELAY_MS = 24 * 60 * 60_000;

/** JWT payload 의 exp(초)를 꺼낸다. mock 토큰(mock.<id>.<ts>)처럼 JWT 가 아니면 null. */
function readExp(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof payload.exp === "number" && Number.isFinite(payload.exp) ? payload.exp : null;
  } catch {
    return null;
  }
}

function scheduleRefresh(token: string | null) {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  if (!token) return;
  const exp = readExp(token);
  if (exp === null) return;
  const delay = Math.min(Math.max(exp * 1000 - Date.now() - REFRESH_LEAD_MS, MIN_REFRESH_DELAY_MS), MAX_REFRESH_DELAY_MS);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void refreshAccessToken().then((next) => {
      // 성공하면 tokenStore.set 이 다음 타이머를 다시 잡는다
      if (next) return;
      tokenStore.set(null);
      onSessionExpired?.();
    });
  }, delay);
}

export const tokenStore = {
  get: () => accessToken,
  set: (t: string | null) => {
    accessToken = t;
    // 토큰이 바뀌거나 지워질 때마다 타이머를 다시 잡는다 — 로그인 간 타이머가 남지 않는다
    scheduleRefresh(t);
  },
};

/**
 * refresh 까지 실패해 세션이 끝났을 때 알릴 콜백.
 * store 를 직접 import 하면 client ↔ store 순환 참조가 생기므로 등록 방식으로 둔다.
 */
let onSessionExpired: (() => void) | null = null;
export function setSessionExpiredHandler(fn: (() => void) | null) {
  onSessionExpired = fn;
}

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

type Query = object;

export function qs(params?: Query) {
  if (!params) return "";
  const sp = new URLSearchParams();
  Object.entries(params as Record<string, unknown>).forEach(([k, v]) => {
    if (v === undefined || v === null || v === "") return;
    sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : "";
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** 401 TOKEN_EXPIRED 시 refresh 후 1회 재시도 */
  retry?: boolean;
}

let refreshing: Promise<string | null> | null = null;
async function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "include" })
      .then(async (r) => {
        if (!r.ok) return null;
        const data = (await r.json()) as { accessToken: string };
        tokenStore.set(data.accessToken);
        return data.accessToken;
      })
      .catch(() => null)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, {
    method: opts.method ?? "GET",
    headers,
    credentials: "include",
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });

  if (res.status === 204) return undefined as T;
  if (res.ok) return (await res.json()) as T;

  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await res.json()) as ApiErrorBody;
  } catch {
    body = null;
  }

  if (res.status === 401 && body?.code === "TOKEN_EXPIRED" && opts.retry !== false) {
    const next = await refreshAccessToken();
    if (next) return request<T>(path, { ...opts, retry: false });
    // refresh 만료·무효 → 화면을 게스트 상태로 되돌려 보호 API 반복 401 을 끊는다
    // 동시에 실패한 다른 요청들이 이미 토큰을 비웠다면 중복 알림이므로 한 번만 알린다
    const wasSet = tokenStore.get() !== null;
    tokenStore.set(null);
    if (wasSet) onSessionExpired?.();
  } else if (res.status === 401 && body?.code === "TOKEN_INVALID" && token) {
    // 토큰을 들고 갔는데 무효라면 되살릴 방법이 없다. 토큰 없이 온 401 은 그냥 게스트 요청이므로 건드리지 않는다
    const wasSet = tokenStore.get() !== null;
    tokenStore.set(null);
    if (wasSet) onSessionExpired?.();
  }
  throw new ApiError(res.status, body);
}
