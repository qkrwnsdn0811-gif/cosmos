import type * as T from "./types";

/**
 * 프론트가 백엔드에 기대하는 호출 계약 (docs/api-specification.md). 구현은 api/live.ts 하나다.
 * 화면 코드는 이 인터페이스만 의존하므로 경로·응답 보정은 live.ts 한 곳에서만 다룬다.
 */
export interface CosmosApi {
  auth: {
    checkEmail(email: string): Promise<T.Availability>;
    checkNickname(nickname: string): Promise<T.Availability>;
    login(body: T.LoginRequest): Promise<T.TokenResponse>;
    googleConfig(): Promise<T.GoogleConfig>;
    googleLogin(credential: string): Promise<T.TokenResponse>;
    logout(): Promise<void>;
    refresh(): Promise<T.TokenResponse>;
    signup(body: T.SignupRequest): Promise<T.SignupResponse>;
    sendEmailCode(body: T.EmailSendCodeRequest): Promise<T.EmailSendCodeResponse>;
    verifyEmailCode(body: T.EmailVerifyCodeRequest): Promise<T.EmailVerifyCodeResponse>;
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
    /** 시가총액·규모·PER·PBR·배당수익률. 값이 없으면 각 칸이 null */
    fundamental(companyId: string): Promise<T.Fundamental>;
    industries(): Promise<{ items: T.Industry[] }>;
    /** 기업 탭 시세판: 활성 기업 전부(페이지 없음) */
    quotes(): Promise<T.CompanyQuotes>;
    /** 공개 은하 스냅샷 1년 관계 기업과 그 시세, 점수 내림차순 */
    related(companyId: string): Promise<T.RelatedCompanies>;
  };
  market: {
    /** 코스피·코스닥. 기간 1M·3M·6M·1Y = 거래일 20·60·120·250개 + 기준점 1개 */
    indices(period?: T.PricePeriod): Promise<T.MarketIndices>;
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
  events: {
    /** size 는 기본 20 · 최대 100 */
    list(params?: T.EventListParams): Promise<T.Page<T.EventSummary>>;
    detail(eventId: string): Promise<T.EventMap>;
    /** 뉴스 5거래일 전 ~ 20거래일 뒤 종목·지수 종가와 누적 초과수익률 */
    chart(eventId: string, companyId: string): Promise<T.EventChart>;
    /** 방향 딱지 적중률 (학습 기간 뒤 뉴스) */
    backtest(valueChain: T.ValueChain): Promise<T.Backtest>;
    /** 한 종목에 영향을 준 과거 뉴스와 그때의 주가 반응 */
    companyHistory(params: T.CompanyHistoryParams): Promise<T.Page<T.CompanyHistoryItem>>;
  };
  community: {
    latest(params?: { cursor?: string; size?: number }): Promise<T.Page<T.GlobalComment>>;
    byCompany(companyId: string, params?: { cursor?: string; size?: number }): Promise<T.Page<T.CompanyComment>>;
    write(companyId: string, content: string): Promise<T.CommentWriteResponse>;
    edit(commentId: string, content: string): Promise<T.CommentEditResponse>;
    remove(commentId: string): Promise<void>;
  };
}
