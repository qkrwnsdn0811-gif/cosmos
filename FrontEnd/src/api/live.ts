import type { CosmosApi } from "./contract";
import { qs, request, tokenStore } from "./client";
import { rememberCompanyLogoRefs } from "@/lib/logos";
import type * as T from "./types";

/* ------------------------------ 응답 보정 ------------------------------ */
/**
 * 명세는 "값이 존재하지 않는 선택 필드는 null" 인데, 백엔드 기업 DTO 일부가 `Objects.toString(x, "")` 로
 * 빈 문자열을 내려준다(CompanyDetailResponse·CompanySummaryResponse·IndustryResponse.description).
 * 화면 타입은 명세대로 null 을 기대하므로 경계에서 한 번만 맞춘다. 백엔드가 고쳐지면 그대로 통과한다.
 */
const nz = (s: string | null | undefined): string | null => (s == null || s === "" ? null : s);
/** 대표 산업이 없는 기업은 `{industryId: null, name: null}` 꼴로 올 수 있다 → null 로 접는다 */
const nzIndustry = (r: Partial<T.IndustryRef> | null | undefined): T.IndustryRef | null =>
  r && r.industryId && r.name ? { industryId: r.industryId, name: r.name } : null;
const mapPage = <A, B>(page: T.Page<A>, f: (a: A) => B): T.Page<B> => ({ ...page, items: page.items.map(f) });
/**
 * 시장·종목코드를 함께 주는 응답이 지나갈 때 로고 참조를 적어 둔다.
 * companyId·name 만 주는 응답(기업 중심 그래프·뉴스의 관련 기업)에서도 아바타가 로고를 찾게 하려는 캐시다 (src/lib/logos).
 */
const remember = <R>(res: R, pick: (r: R) => readonly { companyId: string; market: T.Market; stockCode: string | null }[]): R => {
  rememberCompanyLogoRefs(pick(res));
  return res;
};

const normSummary = (c: T.CompanySummary): T.CompanySummary => ({
  ...c,
  nameEn: nz(c.nameEn),
  stockCode: nz(c.stockCode),
  primaryIndustry: nzIndustry(c.primaryIndustry),
});
const normDetail = (c: T.CompanyDetail): T.CompanyDetail => ({
  ...c,
  nameEn: nz(c.nameEn),
  stockCode: nz(c.stockCode),
  description: nz(c.description),
});
const normIndustry = (i: T.Industry): T.Industry => ({ ...i, description: nz(i.description) });
const normWatch = (w: T.WatchCompanyItem): T.WatchCompanyItem => ({
  ...w,
  stockCode: nz(w.stockCode),
  primaryIndustry: nzIndustry(w.primaryIndustry),
});

/** 실 백엔드(/api) 호출 구현. 경로·파라미터·상태 코드는 docs/api-specification.md 를 따른다. */
export const liveApi: CosmosApi = {
  auth: {
    checkEmail: (email) => request(`/auth/check-email${qs({ email })}`),
    checkNickname: (nickname) => request(`/auth/check-nickname${qs({ nickname })}`),
    login: async (body) => {
      const res = await request<T.TokenResponse>("/auth/login", { method: "POST", body, retry: false });
      tokenStore.set(res.accessToken);
      return res;
    },
    // retry 를 막지 않는다 — 만료된 access 토큰으로 오면 서버 필터가 컨트롤러 앞에서 401 을 내 refresh 토큰이 폐기되지 않으므로,
    // client.ts 의 TOKEN_EXPIRED → refresh → 1회 재시도를 타서 실제로 서버 세션을 끝낸다
    logout: async () => {
      try {
        await request<void>("/auth/logout", { method: "POST" });
      } finally {
        tokenStore.set(null);
      }
    },
    // refresh_token 쿠키(Path=/api/auth, HttpOnly)가 자동 첨부된다 — client.ts 가 모든 요청을 credentials: include 로 보낸다
    refresh: async () => {
      const res = await request<T.TokenResponse>("/auth/refresh", { method: "POST", retry: false });
      tokenStore.set(res.accessToken);
      return res;
    },
    signup: (body) => request("/auth/signup", { method: "POST", body, retry: false }),
    // 공개 엔드포인트 — 409 EMAIL_DUPLICATED · 429 EMAIL_CODE_TOO_FREQUENT · 400 EMAIL_CODE_INVALID/EMAIL_CODE_EXPIRED 는 폼이 코드로 구분한다
    sendEmailCode: (body) => request("/auth/email/send-code", { method: "POST", body, retry: false }),
    verifyEmailCode: (body) => request("/auth/email/verify-code", { method: "POST", body, retry: false }),
  },
  users: {
    me: () => request("/users/me"),
    updateNickname: (nickname) => request("/users/me", { method: "PATCH", body: { nickname } }),
    scraps: (p) => request(`/users/me/scraps${qs(p)}`),
    addScrap: (newsId) => request(`/users/me/scraps/${newsId}`, { method: "POST" }),
    removeScrap: (newsId) => request(`/users/me/scraps/${newsId}`, { method: "DELETE" }),
    watchCompanies: async (p) => remember(mapPage(await request<T.Page<T.WatchCompanyItem>>(`/users/me/watch-companies${qs(p)}`), normWatch), (r) => r.items),
    addWatch: (companyId) => request(`/users/me/watch-companies/${companyId}`, { method: "POST" }),
    removeWatch: (companyId) => request(`/users/me/watch-companies/${companyId}`, { method: "DELETE" }),
  },
  companies: {
    list: async (p) => remember(mapPage(await request<T.Page<T.CompanySummary>>(`/companies${qs(p)}`), normSummary), (r) => r.items),
    detail: async (id) => remember(normDetail(await request<T.CompanyDetail>(`/companies/${id}`)), (c) => [c]),
    metrics: (id, window) => request(`/companies/${id}/metrics${qs({ window })}`),
    metricsHistory: (id, params) => request(`/companies/${id}/metrics/history${qs(params)}`),
    // interval 은 MVP 에서 1D 만 지원한다 (명세 Query Params)
    stockPrices: (id, period) => request(`/companies/${id}/stock-prices${qs({ period, interval: "1D" })}`),
    industries: async () => {
      const res = await request<{ items: T.Industry[] }>("/industries");
      return { items: res.items.map(normIndustry) };
    },
  },
  news: {
    list: (p) => request(`/news${qs(p)}`),
    detail: (id) => request(`/news/${id}`),
  },
  graphs: {
    latest: async (p) => remember(await request<T.LatestGraph>(`/graphs/latest${qs(p)}`), (g) => g.nodes),
    company: (id, p) => request(`/graphs/companies/${id}${qs(p)}`),
    relationship: (id, window) => request(`/relationships/${id}${qs({ window })}`),
    evidence: (id, p) => request(`/relationships/${id}/evidence${qs(p)}`),
  },
  community: {
    latest: (p) => request(`/community/comments${qs(p)}`),
    byCompany: (id, p) => request(`/companies/${id}/comments${qs(p)}`),
    write: (id, content) => request(`/companies/${id}/comments`, { method: "POST", body: { content } }),
    edit: (id, content) => request(`/comments/${id}`, { method: "PATCH", body: { content } }),
    remove: (id) => request(`/comments/${id}`, { method: "DELETE" }),
  },
};
