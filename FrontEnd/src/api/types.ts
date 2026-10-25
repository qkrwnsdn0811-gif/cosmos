/**
 * docs/api-specification.md 의 응답 계약을 그대로 옮긴 타입.
 * 필드명은 camelCase, 식별자는 UUID 문자열, 시각은 ISO 8601 UTC.
 */
export type UUID = string;
export type ISODateTime = string;
/** 사용자 식별자만 64비트 정수(users.user_id BIGSERIAL). 그 밖의 식별자는 UUID 문자열이다. */
export type UserId = number;

export type Market = "KOSPI" | "KOSDAQ" | "NASDAQ" | (string & {});
/** 기업 인텔리전스의 뉴스·관계 지표(CompanyMetrics·MetricsHistory)와 단일 관계 상세(RelationshipDetail)가 쓰는 기간 —
    api 요청 값은 그대로 30D/1Y/10Y 이고, 화면 표기(1M/1Y/10Y)는 lib/meta 의 METRIC_WINDOW_LABEL 이 맡는다.
    주가 고도(AltitudeWindow, 아래)와는 값·목적이 다른 완전히 별개 기간이다 — 서로 다른 화면 조각
    (ControlDock 의 주가 고도 ↔ CompanyPanel/RelationshipPanel) 이 각자 고른다.
    그래프 간선(LatestGraphParams·CompanyGraphParams 의 window, 아래)도 같은 값 체계를 쓰지만 store/galaxy 의
    별도 상태(edgeWindow) 가 고른다 — "관계선 자체를 어느 기간 기준으로 계산할지" 는 "이미 고른 관계의 지표를
    어느 기간으로 볼지" 와는 다른 선택이라 서로 영향을 주지 않는다 */
export type MetricWindow = "30D" | "1Y" | "10Y";
export type PricePeriod = "1M" | "3M" | "6M" | "1Y";
export type Sentiment = "POSITIVE" | "NEGATIVE" | "NEUTRAL";
export type ImpactDirection = "POSITIVE" | "NEGATIVE" | "NEUTRAL";
export type Directionality = "DIRECTED" | "UNDIRECTED";
/** 관계 유형 코드. DB 설계의 공급·투자·협력·경쟁을 기준으로 하되 미정 코드는 문자열로 수용한다. */
export type RelationshipType = "SUPPLY" | "INVEST" | "PARTNER" | "COMPETE" | (string & {});

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
  hasNext: boolean;
}

export interface FieldError {
  field: string;
  message: string;
}
export interface ApiErrorBody {
  code: string;
  message: string;
  fieldErrors: FieldError[];
}

/* ------------------------------ auth ------------------------------ */
export interface LoginRequest {
  email: string;
  password: string;
}
export interface TokenResponse {
  accessToken: string;
}
export interface SignupRequest {
  email: string;
  password: string;
  nickname: string;
}
export interface SignupResponse {
  userId: UserId;
  email: string;
  nickname: string;
}
/** 가입 전 이메일 인증 — 순서는 코드 발송(send-code) → 코드 확인(verify-code) → 가입. 서버는 이메일을 소문자로 정규화해 돌려준다 */
export interface EmailSendCodeRequest {
  email: string;
}
export interface EmailSendCodeResponse {
  email: string;
  /** 코드 유효 시간(초, 기본 300) — 남은 시간 표시에 쓴다 */
  expiresInSeconds: number;
}
export interface EmailVerifyCodeRequest {
  email: string;
  /** 숫자 6자리 */
  code: string;
}
export interface EmailVerifyCodeResponse {
  email: string;
  /** 항상 true — 실패는 오류 응답(EMAIL_CODE_INVALID·EMAIL_CODE_EXPIRED)으로 온다 */
  verified: boolean;
  /** 인증 완료 상태가 유지되는 시간(초, 기본 1800). 이 안에 가입을 마쳐야 한다 */
  validForSeconds: number;
}
export interface Availability {
  available: boolean;
}

/* ------------------------------ users ------------------------------ */
export interface Me {
  userId: UserId;
  email: string;
  nickname: string;
}
export interface ScrapItem {
  newsId: UUID;
  title: string;
  /** 뉴스 목록(NewsItem)과 같은 원본 컬럼 — 요약·매체·발행 시각은 NULL 허용 */
  summary: string | null;
  publisher: string | null;
  originalUrl: string;
  publishedAt: ISODateTime | null;
  scrappedAt: ISODateTime;
}
export interface IndustryRef {
  industryId: UUID;
  name: string;
}
export interface WatchCompanyItem {
  companyId: UUID;
  name: string;
  stockCode: string | null;
  market: Market;
  /** 대표 산업이 지정되지 않은 기업은 null (WatchCompanyItemResponse 주석) */
  primaryIndustry: IndustryRef | null;
  watchedAt: ISODateTime;
}
/* ---------------------------- companies ---------------------------- */
export interface CompanySummary {
  companyId: UUID;
  name: string;
  nameEn: string | null;
  stockCode: string | null;
  market: Market;
  /** 대표 산업이 지정되지 않은 기업은 null (백엔드 company_industry.is_primary 미지정) */
  primaryIndustry: IndustryRef | null;
  watched: boolean;
}
export interface CompanyIndustry extends IndustryRef {
  primary: boolean;
}
export interface CompanyDetail {
  companyId: UUID;
  name: string;
  nameEn: string | null;
  stockCode: string | null;
  market: Market;
  description: string | null;
  industries: CompanyIndustry[];
  watched: boolean;
  /**
   * 시가총액·상장주식수는 V1 DDL 의 company 테이블에도 명세 응답에도 없다.
   * 백엔드가 열어 줄 때 화면이 그대로 받을 수 있도록 선택 필드로만 자리를 둔다 — 지금은 항상 undefined 이고 화면은 그 칸을 접는다.
   */
  marketCap?: number | null;
  listedShares?: number | null;
}
export interface CompanyMetrics {
  companyId: UUID;
  window: MetricWindow;
  newsMentionCount: number | null;
  positiveCount: number | null;
  negativeCount: number | null;
  sentimentScore: number | null;
  relationshipCount: number | null;
  measuredAt: ISODateTime | null;
}
export interface MetricPoint {
  measuredAt: ISODateTime;
  newsMentionCount: number;
  positiveCount: number;
  negativeCount: number;
  sentimentScore: number | null;
  relationshipCount: number;
}
export interface MetricsHistoryParams {
  window?: MetricWindow;
  /** YYYY-MM-DD */
  from?: string;
  to?: string;
}
export interface MetricsHistory {
  companyId: UUID;
  window: MetricWindow;
  items: MetricPoint[];
}
export interface Candle {
  tradingAt: ISODateTime;
  openPrice: number;
  highPrice: number;
  lowPrice: number;
  closePrice: number;
  tradingVolume: number;
}
export interface StockPrices {
  companyId: UUID;
  period: PricePeriod;
  /** MVP 는 1D 만 지원하지만 stock_price_history.interval_type 은 자유값이라 확장을 열어 둔다. */
  interval: "1D" | (string & {});
  /** 명세는 항상 값이 있다고 하지만 백엔드는 시계열이 없는 기업에 null 을 내려준다 (items: []) */
  asOfAt: ISODateTime | null;
  items: Candle[];
}
export interface Industry {
  industryId: UUID;
  parentIndustryId: UUID | null;
  name: string;
  description: string | null;
  companyCount: number;
}
export interface CompanyListParams {
  keyword?: string;
  market?: Market;
  industryId?: UUID;
  cursor?: string;
  size?: number;
}

/* ------------------------------ news ------------------------------ */
export interface CompanyRef {
  companyId: UUID;
  name: string;
}
export interface NewsItem {
  newsId: UUID;
  title: string;
  summary: string | null;
  publisher: string | null;
  originalUrl: string;
  publishedAt: ISODateTime | null;
  /** AI 감성 분석이 아직 돌지 않은 기사는 null 로 온다 (document_analysis 미적재) */
  sentiment: Sentiment | null;
  relatedCompanies: CompanyRef[];
  scrapped: boolean;
}
export interface NewsRelatedCompany extends CompanyRef {
  /** 기업별 감성도 분석 전에는 null */
  sentiment: Sentiment | null;
  relevanceScore: number | null;
  impactScore: number | null;
}
export interface NewsEvidence {
  sentence: string;
  confidence: number | null;
}
export interface NewsDetail {
  newsId: UUID;
  title: string;
  summary: string | null;
  publisher: string | null;
  author: string | null;
  originalUrl: string;
  publishedAt: ISODateTime | null;
  relatedCompanies: NewsRelatedCompany[];
  evidence: NewsEvidence[];
  scrapped: boolean;
}
export interface NewsListParams {
  keyword?: string;
  companyId?: UUID;
  industryId?: UUID;
  sentiment?: Sentiment;
  from?: string;
  to?: string;
  cursor?: string;
  size?: number;
}

/* ------------------------------ graph ------------------------------ */
export interface GraphEdge {
  relationshipId: UUID;
  sourceCompanyId: UUID;
  targetCompanyId: UUID;
  relationshipType: RelationshipType;
  /** 0 ~ 100. 시스템 기본 비율(뉴스 50 · 공시 50)로 계산한 공통 점수 */
  score: number;
  /**
   * 사용자가 화면에서 비율을 조절할 때 쓰는 구성 점수 (lib/score 의 blendScore).
   * 근거가 없으면 null 이고, 서버가 아직 필드를 내려주지 않을 수 있어 선택 필드로 둔다 — 그때는 score 만 쓴다.
   */
  newsScore?: number | null;
  disclosureScore?: number | null;
  impactDirection: ImpactDirection | null;
}
/** 은하 뷰가 행성 높이로 쓰는 등락률 기간 — 그대로 7D/30D/90D 를 쓴다. MetricWindow(30D/1Y/10Y, 위)와는
    값도 표기도 다른 별개 기간이다: ControlDock 의 주가 고도 선택과 CompanyPanel/RelationshipPanel 의
    뉴스·관계 지표·연결 선 기간 선택은 서로 영향을 주지 않고 각자 고를 수 있다 */
export type AltitudeWindow = "7D" | "30D" | "90D";
export interface LatestGraphNode {
  companyId: UUID;
  name: string;
  stockCode: string | null;
  market: Market;
  /** 대표 산업이 없는 기업은 null — 은하 배치(lib/graph)는 '미분류' 성단으로 모은다 */
  industryName: string | null;
  /**
   * 기간별 주가 등락률(퍼센트, 예 -3.2). 은하 뷰의 행성 높이가 이 값을 쓴다.
   * 서버가 아직 내려주지 않을 수 있어 선택 필드이고, 시계열이 없는 기업은 null 이다 (README '백엔드에 확인이 필요한 항목').
   * 키를 7D/30D/90D 로 맞췄다 — 서버 응답도 이 키로 내려줘야 은하 고도가 실제로 반영된다(백엔드 확인 필요).
   */
  priceChange?: { "7D"?: number | null; "30D"?: number | null; "90D"?: number | null };
  /**
   * 원화 환산 시가총액. 은하 뷰의 행성 크기가 연결 가중치와 함께 이 값을 쓴다.
   * 시장 통화가 달라도 한 척도로 비교해야 하므로 서버가 환산해 준다고 가정한다(README '백엔드에 확인이 필요한 항목').
   * 서버가 아직 내려주지 않을 수 있어 선택 필드이고, 값이 없는 기업은 연결 가중치만으로 크기를 정한다.
   */
  marketCapKrw?: number | null;
}
export interface LatestGraph {
  snapshotId: UUID;
  asOfAt: ISODateTime;
  nextRefreshAt: ISODateTime;
  personalized: boolean;
  nodes: LatestGraphNode[];
  edges: GraphEdge[];
}
export interface LatestGraphParams {
  universe?: string;
  industryId?: UUID;
  /** 관계선(간선) 계산의 기준 기간 — 주면 그 기간 기준으로 점수·관계를 다시 계산해 edges 가 달라질 수 있다 (백엔드 확인됨, 2026-09-22) */
  window?: MetricWindow;
}
export interface CompanyGraphNode {
  companyId: UUID;
  name: string;
  /** 0 = 중심 기업, 1 = 직접 관계, 2·3 = 간접 관계 */
  depth: number;
}
export interface CompanyGraph {
  snapshotId: UUID;
  asOfAt: ISODateTime;
  centerCompanyId: UUID;
  personalized: boolean;
  nodes: CompanyGraphNode[];
  edges: GraphEdge[];
}
export interface CompanyGraphParams {
  /** 1~3, 기본 3 (명세 Query Params) */
  maxDepth?: number;
  /** 관계선(간선) 계산의 기준 기간 — LatestGraphParams.window 와 같은 값 체계 (백엔드 확인됨, 2026-09-22) */
  window?: MetricWindow;
}
export interface RelationshipDetail {
  relationshipId: UUID;
  sourceCompany: CompanyRef;
  targetCompany: CompanyRef;
  relationshipType: RelationshipType;
  directionality: Directionality;
  window: MetricWindow;
  /** 공통 점수 — 구성 점수는 GraphEdge 와 같은 규칙으로 화면에서만 섞는다 */
  score: number;
  newsScore?: number | null;
  disclosureScore?: number | null;
  impactDirection: ImpactDirection | null;
  confidence: number | null;
  evidenceCount: number;
  asOfAt: ISODateTime;
  personalized: boolean;
}
export interface RelationshipEvidence {
  newsId: UUID;
  title: string;
  summary: string | null;
  publisher: string | null;
  originalUrl: string;
  publishedAt: ISODateTime | null;
  /**
   * NEWS 면 evidenceSentence 가 기사 원문에서 그대로 오려낸 문장이고,
   * DISCLOSURE 면 공시 내용을 요약해 만든 문장이다. 둘을 같은 모양으로 그리면
   * 사용자가 인용문과 생성문을 구분하지 못하므로 표시를 나눈다.
   */
  documentType: "NEWS" | "DISCLOSURE" | (string & {});
  /** 함께 언급(CO_MENTIONED) 근거는 문장 원문이 없어 null 이다 — evidenceText() 가 제목으로 대신 보여 준다 */
  evidenceSentence: string | null;
  /** 그 문장을 만든 모델·규칙. 공시 요약은 생성 모델 이름이 들어온다 */
  modelVersion: string | null;
  contributionScore: number | null;
  confidence: number | null;
}

/* ---------------------------- community ---------------------------- */
export interface CommentAuthor {
  userId: UserId;
  nickname: string;
}
export interface CompanyComment {
  commentId: UUID;
  content: string;
  author: CommentAuthor;
  companyId: UUID;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
  edited: boolean;
}
/** 전체 커뮤니티 목록의 기업 — 로고를 찾을 수 있게 종목코드·시장이 함께 온다 (없는 기업은 null) */
export interface CommentCompanyRef extends CompanyRef {
  stockCode: string | null;
  market: Market | null;
}
export interface GlobalComment extends Omit<CompanyComment, "companyId"> {
  company: CommentCompanyRef;
}
export interface CommentWriteResponse {
  commentId: UUID;
  companyId: UUID;
  content: string;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}
export interface CommentEditResponse {
  commentId: UUID;
  content: string;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
  edited: boolean;
}

/* ---------------- NEXUS 이벤트 (docs/api-specification.md "events") ---------------- */
export type ValueChain = "SEMICONDUCTOR" | "BATTERY" | "AUTO";
/** REPORTED = 기사에 직접 언급(근거), INFERRED = 공급망을 타고 추정 */
export type ImpactBasis = "REPORTED" | "INFERRED";
/** HOLD = 판단 보류 (holdReason 동반) */
export type ImpactStatus = "OK" | "HOLD";
/** 추정 경로 한 단계의 출처 — COSMOS 기존 관계 · NEWS 빅카인즈 근거 · COMENTION 같은 문장 동시언급 */
export type EdgeSource = "COSMOS" | "NEWS" | "COMENTION" | (string & {});

export interface EventCompanyRef {
  companyId: UUID;
  name: string;
  stockCode: string | null;
  market: Market | null;
}
/** 뉴스 유형 코드 (nexus/nexus/cases/newstype.py). 라벨은 응답의 newsTypeLabel 을 쓴다 */
export type NewsTypeCode =
  | "ACCIDENT" | "EXPORT_CONTROL" | "TARIFF" | "EARNINGS_BEAT" | "EARNINGS_MISS" | "SUPPLY_DEAL" | "PRICE_UP"
  | "PRICE_DOWN" | "PRODUCTION_CUT" | "CAPEX" | "SHAREHOLDER" | "AI_DEMAND" | "POLICY" | "ANALYST" | "MARKET_WRAP"
  | "OTHER";
/** 투자 기간 — SHORT 3거래일, LONG 20거래일 */
export type Horizon = "SHORT" | "LONG";
/** 방향 딱지 — 과거 사례 통계이며 예측이 아니다 */
export type SignalLabel = "UP" | "DOWN" | "HOLD";
/** 기업 위치 — 기사에 나옴 또는 추정 경로 마지막 단계의 관계 */
export type CompanyPosition = "REPORTED" | "SUPPLIER" | "CUSTOMER" | "COMPETITOR" | "PARTNER" | "INVEST" | "COMENTION";
export interface SignalCount {
  up: number;
  down: number;
  hold: number;
}
export interface EventSignal {
  horizon: Horizon;
  windowDays: number;
  label: SignalLabel;
  /** HOLD 일 때 사유 (사례 부족 · 오른 경우와 내린 경우가 비슷함 · 반응이 작음 · 비슷한 유형 없음 · 연결이 약함) */
  holdReason: string | null;
  /** COMPANY = 그 종목의 과거 사례, POSITION = 같은 위치 기업 전체 사례 */
  caseScope: "COMPANY" | "POSITION" | null;
  caseCount: number;
  /** 많은 쪽으로 움직인 사례 비율 (0~1) */
  sameShare: number | null;
  /** 과거 사례 평균 초과수익률 (비율) */
  meanAr: number | null;
  /** 과거 사례 중 하락 비율 (0~1) */
  downShare: number | null;
}
export interface Fundamental {
  marketCap: number | null;
  marketCapRank: number | null;
  sizeClass: "LARGE" | "MID" | "SMALL" | null;
  /** 적자거나 재무제표가 없으면 null */
  per: number | null;
  pbr: number | null;
  /** 비율(0.012 = 1.2%) */
  dividendYield: number | null;
  netIncomeTtm: number | null;
  equity: number | null;
  dividendPerShare: number | null;
  closePrice: number | null;
  priceDate: string | null;
  fiscalBasis: string | null;
}
export interface ChartPoint {
  /** 0 = 뉴스 거래일 */
  offset: number;
  date: string;
  close: number | null;
  indexClose: number | null;
  /** 뉴스 전날 종가 대비 누적 수익률 (비율) */
  stockReturn: number | null;
  indexReturn: number | null;
  abnormalReturn: number | null;
}
export interface EventChart {
  eventId: UUID;
  companyId: UUID;
  tradingDate: string;
  benchmark: "KOSPI" | "KOSDAQ";
  points: ChartPoint[];
}
export interface BacktestRow {
  horizon: Horizon;
  /** null = 전체 유형 */
  newsType: NewsTypeCode | null;
  newsTypeLabel: string | null;
  caseCount: number;
  labeledCount: number;
  hitCount: number;
  hitRate: number | null;
  upLabels: number;
  downLabels: number;
  baseDownShare: number | null;
  /** 무조건 많은 쪽으로 답할 때의 적중률 */
  baselineRate: number | null;
}
export type MoveGrade = "SMALL" | "MID" | "LARGE";
/** 예상 변동폭 검증 한 줄 — 학습 기간 뒤 뉴스일에서 그 등급 종목의 예상과 실제 |초과수익률| 평균 */
export interface MoveBacktestRow {
  horizon: Horizon;
  windowDays: number;
  grade: MoveGrade;
  caseCount: number;
  predictedMove: number | null;
  realizedMove: number | null;
}
export interface Backtest {
  valueChain: ValueChain;
  trainEnd: string | null;
  testStart: string | null;
  testEnd: string | null;
  rows: BacktestRow[];
  /** SHORT 먼저, 기간 안에서 SMALL · MID · LARGE 순 */
  moveRows: MoveBacktestRow[];
}
/** 종목의 예상 변동폭 — 학습 기간 이 종목 뉴스일 뒤 |초과수익률| 평균(방향과 무관). 종목마다 기간별로 하나 */
export interface ExpectedMove {
  horizon: Horizon;
  windowDays: number;
  expectedMove: number;
  caseCount: number;
  grade: MoveGrade;
}
export interface CompanyHistoryItem {
  event: EventSummary;
  basis: ImpactBasis;
  position: CompanyPosition | null;
  positionLabel: string | null;
  signals: EventSignal[];
  abnormalReturns: AbnormalReturn[];
}
export interface EventSummary {
  eventId: UUID;
  /** /api/news/{newsId} 와 같은 문서 ID */
  newsId: UUID;
  title: string;
  summary: string | null;
  publisher: string | null;
  /** 빈 문자열이면 원문 링크 없음 */
  originalUrl: string;
  dateline: ISODateTime;
  /** 초과수익률 기준 거래일(YYYY-MM-DD). 아직 정해지지 않았으면 null */
  tradingDate: string | null;
  valueChain: ValueChain;
  categories: string[];
  reportedCompanies: EventCompanyRef[];
  inferredCount: number;
  holdCount: number;
  newsType: NewsTypeCode | null;
  newsTypeLabel: string | null;
  /** 이 뉴스의 영향 종목 딱지 수 (기간별) */
  signalCounts: Record<Horizon, SignalCount>;
}
export interface EvidenceSnippet {
  bigkindsNewsId: string;
  title: string;
  publisher: string | null;
  publishedAt: ISODateTime;
  originalUrl: string;
  snippet: string;
  confidence: number | null;
}
export interface PathStep {
  sourceCompanyId: UUID | null;
  sourceName: string | null;
  targetCompanyId: UUID | null;
  targetName: string | null;
  relationshipType: RelationshipType | null;
  edgeSource: EdgeSource | null;
  strength: number | null;
  evidence: EvidenceSnippet[];
}
export interface AbnormalReturn {
  windowDays: number;
  benchmark: string;
  stockReturn: number | null;
  benchmarkReturn: number | null;
  /** 비율(0.017 = 1.7%p). 관측값이며 예측이 아니다 */
  abnormalReturn: number | null;
}
export interface EventNode {
  companyId: UUID | null;
  name: string;
  stockCode: string | null;
  market: Market | null;
  /** 대표 업종 (예: 반도체 장비), 없으면 null */
  industry: string | null;
  basis: ImpactBasis;
  hop: number;
  score: number | null;
  status: ImpactStatus;
  holdReason: string | null;
  /** 영향 모델(nexus predict-impact)이 채운다. confidence가 있는데 impactDirection이 null이면 방향 보류 */
  impactDirection: ImpactDirection | null;
  /** 0~1, 상승 확률과 하락 확률의 차이 */
  impactMagnitude: number | null;
  confidence: number | null;
  anchorCompanyId: UUID | null;
  path: PathStep[];
  abnormalReturns: AbnormalReturn[];
  position: CompanyPosition | null;
  positionLabel: string | null;
  /** SHORT 다음 LONG */
  signals: EventSignal[];
  /** SHORT 다음 LONG, 아직 없으면 빈 목록 */
  expectedMoves: ExpectedMove[];
  fundamental: Fundamental | null;
}
export interface EventMap {
  event: EventSummary;
  nodes: EventNode[];
}
export interface EventListParams {
  valueChain?: ValueChain;
  companyId?: UUID;
  hasInferred?: boolean;
  newsType?: NewsTypeCode;
  cursor?: string;
  size?: number;
}
export interface CompanyHistoryParams {
  companyId: UUID;
  cursor?: string;
  size?: number;
}
