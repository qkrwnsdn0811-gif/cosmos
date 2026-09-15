import type { CosmosApi } from "./contract";
import { qs, request, tokenStore } from "./client";
import type * as T from "./types";

/** 실 백엔드(/api) 호출 구현. 경로·파라미터는 docs/api-specification.md 를 따른다. */
export const liveApi: CosmosApi = {
  auth: {
    checkEmail: (email) => request(`/auth/check-email${qs({ email })}`),
    checkNickname: (nickname) => request(`/auth/check-nickname${qs({ nickname })}`),
    login: async (body) => {
      const res = await request<T.TokenResponse>("/auth/login", { method: "POST", body, retry: false });
      tokenStore.set(res.accessToken);
      return res;
    },
    logout: async () => {
      try {
        await request<void>("/auth/logout", { method: "POST", retry: false });
      } finally {
        tokenStore.set(null);
      }
    },
    refresh: async () => {
      const res = await request<T.TokenResponse>("/auth/refresh", { method: "POST", retry: false });
      tokenStore.set(res.accessToken);
      return res;
    },
    signup: (body) => request("/auth/signup", { method: "POST", body, retry: false }),
  },
  users: {
    me: () => request("/users/me"),
    updateNickname: (nickname) => request("/users/me", { method: "PATCH", body: { nickname } }),
    scraps: (p) => request(`/users/me/scraps${qs(p)}`),
    addScrap: (newsId) => request(`/users/me/scraps/${newsId}`, { method: "POST" }),
    removeScrap: (newsId) => request(`/users/me/scraps/${newsId}`, { method: "DELETE" }),
    watchCompanies: (p) => request(`/users/me/watch-companies${qs(p)}`),
    addWatch: (companyId) => request(`/users/me/watch-companies/${companyId}`, { method: "POST" }),
    removeWatch: (companyId) => request(`/users/me/watch-companies/${companyId}`, { method: "DELETE" }),
  },
  companies: {
    list: (p) => request(`/companies${qs(p)}`),
    detail: (id) => request(`/companies/${id}`),
    metrics: (id, window) => request(`/companies/${id}/metrics${qs({ window })}`),
    metricsHistory: (id, params) => request(`/companies/${id}/metrics/history${qs(params)}`),
    stockPrices: (id, period) => request(`/companies/${id}/stock-prices${qs({ period, interval: "1D" })}`),
    industries: () => request("/industries"),
  },
  news: {
    list: (p) => request(`/news${qs(p)}`),
    detail: (id) => request(`/news/${id}`),
  },
  graphs: {
    latest: (p) => request(`/graphs/latest${qs(p)}`),
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
