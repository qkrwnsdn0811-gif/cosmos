import { useCallback } from "react";
import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api";
import type * as T from "@/api/types";
import { weightedEdges, weightedRelationship } from "@/lib/score";
import { useSession } from "@/store/session";
import { useNewsWeight } from "@/store/weight";

const useKey = () => useSession((s) => s.sessionKey);
const useAuthed = () => useSession((s) => s.status === "authed");
/**
 * 세션 판정이 끝났는지. 세션 키가 키에 들어가는 쿼리는 booting 동안 멈춰 둔다 —
 * guest 키로 한 번, 로그인 확정 후 다시 한 번 받던 콜드 로드 중복 호출을 없앤다.
 * 힌트가 없는 게스트는 첫 렌더부터 "guest" 이므로 이 게이트에 걸리지 않는다.
 */
const useBooted = () => useSession((s) => s.status !== "booting");
/** 쿼리 키에 들어가는 세션 키 — 컴포넌트가 prefetch 옵션을 만들 때 같은 키를 쓰도록 공개한다 */
export const useSessionKey = useKey;

/* ------------------------------ 기준 정보 ------------------------------ */
export function useIndustries() {
  return useQuery({ queryKey: ["industries"], queryFn: () => api.companies.industries(), staleTime: 60 * 60_000 });
}

/* ------------------------------ 그래프 ------------------------------ */
/**
 * 뉴스·공시 비율은 서버에 보내지 않는다 — 받은 구성 점수를 화면에서만 섞는다(lib/score).
 * select 에서 한 번 섞어 두면 씬·목록·경로 등 score 를 읽는 화면이 모두 같은 값을 본다.
 * 캐시(queryKey)는 비율과 무관하므로 슬라이더를 움직여도 다시 요청하지 않는다.
 */
function useWeightedEdges<G extends { edges: T.GraphEdge[] }>() {
  const w = useNewsWeight();
  return useCallback((g: G): G => ({ ...g, edges: weightedEdges(g.edges, w) }), [w]);
}

export function useLatestGraph(industryId: string | null) {
  const k = useKey();
  const booted = useBooted();
  const select = useWeightedEdges<T.LatestGraph>();
  return useQuery({
    queryKey: ["graph", "latest", k, industryId],
    queryFn: () => api.graphs.latest({ industryId: industryId ?? undefined }),
    enabled: booted,
    staleTime: 5 * 60_000,
    placeholderData: keepPreviousData,
    select,
  });
}
/** 기업 그래프 쿼리 옵션 — 훅과 prefetch(호버 선로딩)가 같은 키·함수를 공유하도록 팩토리로 둔다 */
export const companyGraphQuery = (k: string, companyId: string) => ({
  queryKey: ["graph", "company", k, companyId] as const,
  queryFn: () => api.graphs.company(companyId),
  staleTime: 5 * 60_000,
});
export function useCompanyGraph(companyId: string | null) {
  const k = useKey();
  const booted = useBooted();
  const select = useWeightedEdges<T.CompanyGraph>();
  return useQuery({ ...companyGraphQuery(k, companyId ?? ""), enabled: booted && Boolean(companyId), select });
}
export function useRelationship(relationshipId: string | null, window: T.MetricWindow) {
  const k = useKey();
  const booted = useBooted();
  const w = useNewsWeight();
  const select = useCallback((r: T.RelationshipDetail) => weightedRelationship(r, w), [w]);
  return useQuery({
    queryKey: ["relationship", k, relationshipId, window],
    queryFn: () => api.graphs.relationship(relationshipId!, window),
    enabled: booted && Boolean(relationshipId),
    placeholderData: keepPreviousData,
    select,
  });
}
/**
 * 씬 툴팁 3단(B-2)의 대표 근거 1건 — size:1 로 가장 가벼운 요청만 보낸다.
 * 세션 상한(60회): 훅이 실제로 서버에 쏘는 총 횟수를 모듈 변수로 세고, 닿으면 새 요청을 막고 capped 를 알린다.
 * 스침(hover) 필터링은 호출 측(SceneTip)의 300ms 디바운스가 담당 — 여기서 또 디바운스하면 지연이 겹친다.
 */
const TOP_EVIDENCE_SESSION_CAP = 60;
let topEvidenceCallCount = 0;

export function useTopEvidence(relationshipId: string | null) {
  const capped = topEvidenceCallCount >= TOP_EVIDENCE_SESSION_CAP;
  const query = useQuery({
    queryKey: ["relationship", "evidence", "top", relationshipId],
    queryFn: () => {
      topEvidenceCallCount += 1;
      // api.graphs.evidence 는 signal 을 받지 않는다(RequestOptions·contract 어디에도 없음) — react-query 가
      // 키 변경 시 이전 관찰을 알아서 버려 주므로 취소 전달은 생략한다.
      return api.graphs.evidence(relationshipId!, { size: 1 });
    },
    enabled: Boolean(relationshipId) && !capped,
    staleTime: 5 * 60_000,
  });
  return { ...query, capped };
}

export function useRelationshipEvidence(relationshipId: string | null) {
  return useInfiniteQuery({
    queryKey: ["relationship", "evidence", relationshipId],
    queryFn: ({ pageParam }) => api.graphs.evidence(relationshipId!, { cursor: pageParam, size: 10 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: Boolean(relationshipId),
  });
}

/* ------------------------------ 기업 ------------------------------ */
export function useCompany(companyId: string | null) {
  const k = useKey();
  const booted = useBooted();
  return useQuery({ queryKey: ["company", k, companyId], queryFn: () => api.companies.detail(companyId!), enabled: booted && Boolean(companyId) });
}
export function useCompanyMetrics(companyId: string | null, window: T.MetricWindow) {
  return useQuery({
    queryKey: ["company", "metrics", companyId, window],
    queryFn: () => api.companies.metrics(companyId!, window),
    enabled: Boolean(companyId),
    placeholderData: keepPreviousData,
  });
}
export function useMetricsHistory(companyId: string | null, params: T.MetricsHistoryParams) {
  return useQuery({
    queryKey: ["company", "metrics-history", companyId, params],
    queryFn: () => api.companies.metricsHistory(companyId!, params),
    enabled: Boolean(companyId),
    placeholderData: keepPreviousData,
  });
}
export function useStockPrices(companyId: string | null, period: T.PricePeriod) {
  return useQuery({
    queryKey: ["company", "prices", companyId, period],
    queryFn: () => api.companies.stockPrices(companyId!, period),
    enabled: Boolean(companyId),
    placeholderData: keepPreviousData,
  });
}
export function useCompanySearch(keyword: string, enabled = true) {
  const k = useKey();
  const booted = useBooted();
  const q = keyword.trim();
  return useQuery({
    queryKey: ["companies", "search", k, q],
    queryFn: () => api.companies.list({ keyword: q, size: 12 }),
    enabled: booted && enabled && q.length >= 1,
    placeholderData: keepPreviousData,
  });
}
export function useCompanyDirectory(params: T.CompanyListParams) {
  const k = useKey();
  const booted = useBooted();
  return useInfiniteQuery({
    queryKey: ["companies", "directory", k, params],
    queryFn: ({ pageParam }) => api.companies.list({ ...params, cursor: pageParam, size: params.size ?? 40 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: booted,
    placeholderData: keepPreviousData,
  });
}

/* ------------------------------ 뉴스 ------------------------------ */
export function useNewsFeed(params: T.NewsListParams, enabled = true) {
  const k = useKey();
  const booted = useBooted();
  return useInfiniteQuery({
    queryKey: ["news", "feed", k, params],
    queryFn: ({ pageParam }) => api.news.list({ ...params, cursor: pageParam, size: params.size ?? 20 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: booted && enabled,
    placeholderData: keepPreviousData,
  });
}
export function useNewsDetail(newsId: string | null) {
  const k = useKey();
  const booted = useBooted();
  return useQuery({ queryKey: ["news", "detail", k, newsId], queryFn: () => api.news.detail(newsId!), enabled: booted && Boolean(newsId) });
}

/* ------------------------------ 커뮤니티 ------------------------------ */
export function useCompanyComments(companyId: string | null) {
  return useInfiniteQuery({
    queryKey: ["comments", "company", companyId],
    queryFn: ({ pageParam }) => api.community.byCompany(companyId!, { cursor: pageParam, size: 20 }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: Boolean(companyId),
  });
}
export function useLatestComments(size = 8) {
  return useQuery({ queryKey: ["comments", "latest", size], queryFn: () => api.community.latest({ size }), staleTime: 60_000 });
}

/* ------------------------------ 내 정보 ------------------------------ */
export function useWatchlist() {
  const k = useKey();
  const authed = useAuthed();
  return useQuery({ queryKey: ["me", "watchlist", k], queryFn: () => api.users.watchCompanies({ size: 100 }), enabled: authed });
}
export function useScraps() {
  const k = useKey();
  const authed = useAuthed();
  return useQuery({ queryKey: ["me", "scraps", k], queryFn: () => api.users.scraps({ size: 100 }), enabled: authed });
}
/* ------------------------------ 변경 ------------------------------ */
export function useToggleWatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ companyId, watched }: { companyId: string; watched: boolean }) => {
      if (watched) await api.users.removeWatch(companyId);
      else await api.users.addWatch(companyId);
      return !watched;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["company"] });
      void qc.invalidateQueries({ queryKey: ["companies"] });
      void qc.invalidateQueries({ queryKey: ["me", "watchlist"] });
    },
  });
}
export function useToggleScrap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ newsId, scrapped }: { newsId: string; scrapped: boolean }) => {
      if (scrapped) await api.users.removeScrap(newsId);
      else await api.users.addScrap(newsId);
      return !scrapped;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["news"] });
      void qc.invalidateQueries({ queryKey: ["me", "scraps"] });
    },
  });
}
export function useWriteComment(companyId: string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => api.community.write(companyId!, content),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["comments"] });
    },
  });
}
export function useEditComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ commentId, content }: { commentId: string; content: string }) => api.community.edit(commentId, content),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["comments"] });
    },
  });
}
export function useDeleteComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (commentId: string) => api.community.remove(commentId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["comments"] });
    },
  });
}
export function useUpdateNickname() {
  const setUser = useSession((s) => s.setUser);
  return useMutation({
    mutationFn: (nickname: string) => api.users.updateNickname(nickname),
    onSuccess: (me) => setUser(me),
  });
}
