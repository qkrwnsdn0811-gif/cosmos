import type * as T from "./types";

/**
 * 프론트가 백엔드에 기대하는 호출 계약. live(실 API)와 mock(브라우저 내 생성) 두 구현이 있다.
 * 화면 코드는 이 인터페이스만 의존하므로 백엔드 연동 시 api/index.ts 의 선택만 바뀐다.
 */
export interface CosmosApi {
  auth: {
    checkEmail(email: string): Promise<T.Availability>;
    checkNickname(nickname: string): Promise<T.Availability>;
    login(body: T.LoginRequest): Promise<T.TokenResponse>;
    logout(): Promise<void>;
    refresh(): Promise<T.TokenResponse>;
    signup(body: T.SignupRequest): Promise<T.SignupResponse>;
  };
  users: {
    me(): Promise<T.Me>;
    updateNickname(nickname: string): Promise<T.Me>;
    scraps(params?: { cursor?: string; size?: number }): Promise<T.Page<T.ScrapItem>>;
    addScrap(newsId: string): Promise<{ newsId: string; scrappedAt: string }>;
    removeScrap(newsId: string): Promise<void>;
    watchCompanies(params?: { cursor?: string; size?: number }): Promise<T.Page<T.WatchCompanyItem>>;
    addWatch(companyId: string): Promise<{ companyId: string; watchedAt: string }>;
    removeWatch(companyId: string): Promise<void>;
  };
  companies: {
    list(params?: T.CompanyListParams): Promise<T.Page<T.CompanySummary>>;
    detail(companyId: string): Promise<T.CompanyDetail>;
    metrics(companyId: string, window?: T.MetricWindow): Promise<T.CompanyMetrics>;
    metricsHistory(companyId: string, params?: T.MetricsHistoryParams): Promise<T.MetricsHistory>;
    stockPrices(companyId: string, period?: T.PricePeriod): Promise<T.StockPrices>;
    industries(): Promise<{ items: T.Industry[] }>;
  };
  news: {
    list(params?: T.NewsListParams): Promise<T.Page<T.NewsItem>>;
    detail(newsId: string): Promise<T.NewsDetail>;
  };
  graphs: {
    latest(params?: T.LatestGraphParams): Promise<T.LatestGraph>;
    company(companyId: string, params?: T.CompanyGraphParams): Promise<T.CompanyGraph>;
    relationship(relationshipId: string, window?: T.MetricWindow): Promise<T.RelationshipDetail>;
    /** size 는 기본 10 · 최대 20 (명세). 그 이상을 보내면 실 백엔드가 400 을 낸다. */
    evidence(relationshipId: string, params?: { cursor?: string; size?: number }): Promise<T.Page<T.RelationshipEvidence>>;
  };
  community: {
    latest(params?: { cursor?: string; size?: number }): Promise<T.Page<T.GlobalComment>>;
    byCompany(companyId: string, params?: { cursor?: string; size?: number }): Promise<T.Page<T.CompanyComment>>;
    write(companyId: string, content: string): Promise<T.CommentWriteResponse>;
    edit(commentId: string, content: string): Promise<T.CommentEditResponse>;
    remove(commentId: string): Promise<void>;
  };
}
