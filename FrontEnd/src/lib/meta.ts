import type { ImpactDirection, Market, MetricWindow, RelationshipType, Sentiment } from "@/api/types";
import type { CurrencyCode } from "@/lib/format";

/* ------------------------------ 지표·연결 선 기간 ------------------------------ */
/** CompanyPanel(뉴스·관계 지표)·RelationshipPanel(기업 연결 선 근거)이 함께 쓰는 기간 선택지 — api 요청 값 그대로다 */
export const METRIC_WINDOWS: MetricWindow[] = ["30D", "1Y", "10Y"];
/** 위 값의 화면 표기 — api 는 30D 를 쓰지만 화면에는 "1M" 로 보여준다(1Y·10Y 는 표기와 값이 같다) */
export const METRIC_WINDOW_LABEL: Record<MetricWindow, string> = { "30D": "1M", "1Y": "1Y", "10Y": "10Y" };

/* ------------------------------ 산업 팔레트 ------------------------------ */
/**
 * 산업 팔레트 — 키는 industry.name 이다. 여기 없는 산업명은 이름 해시로 FALLBACK_COLORS 에서 고른다(industryColor).
 * 서버 산업 시드가 확정되면 이 키를 실제 이름에 맞춰야 색이 의도대로 붙는다.
 */
export const INDUSTRY_COLORS: Record<string, string> = {
  반도체: "#5FB8FF",
  "2차전지": "#4FE8C0",
  자동차: "#FFB457",
  "인터넷·플랫폼": "#B98CFF",
  "바이오·헬스케어": "#FF7FA4",
  "소프트웨어·AI": "#5CE1E6",
  "소비재·유통": "#B6F26B",
  "전자부품·하드웨어": "#7F8FFF",
  "미디어·게임·엔터": "#E58CFF",
  통신: "#B3D4FF",
  "조선·해운·운송": "#3D8BFF",
  "방산·항공우주": "#8FB9A8",
  "전력·에너지설비": "#FFE27A",
  "정유·화학·에너지": "#FF7A6B",
  "철강·비철·소재": "#C4CBD4",
  "건설·기계·산업재": "#D9B26F",
  금융: "#7BDC6E",
  지주회사: "#D9A0C8",
};
const FALLBACK_COLORS = ["#7FD3FF", "#FFD27F", "#8CFFC9", "#FF9FCF", "#C0C8FF"];

/** 대표 산업이 없는 기업의 표시 이름 — 은하 성단 이름·산업 배지·분포 차트가 같은 라벨을 쓴다 */
export const UNCLASSIFIED_INDUSTRY = "미분류";

export function industryColor(name: string | undefined | null) {
  if (!name || name === UNCLASSIFIED_INDUSTRY) return "#AAB3C2";
  if (INDUSTRY_COLORS[name]) return INDUSTRY_COLORS[name];
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
}

/**
 * 내 은하에서 "관심 등록하지 않은, 이웃으로 딸려 온" 기업의 행성 색 — 산업색 대신 이 어두운 단색을 쓴다.
 * 관심 기업과 이웃이 같은 산업색으로 섞여 보여 혼란을 준다는 지적에 따라(lib/graph layoutMyGalaxy),
 * 우주 배경(#05070f)보다는 밝아 식별은 되면서도 관심 기업의 화려한 산업색과는 확실히 구분되는 무채색을 골랐다.
 */
export const NEIGHBOR_NODE_COLOR = "#454f63";

/* ------------------------------ 관계 유형 ------------------------------ */
export interface RelationshipMeta {
  code: RelationshipType;
  label: string;
  color: string;
  directed: boolean;
  description: string;
}
/**
 * 관계 유형 색 — 화면의 모든 관계 표시가 이 한 곳을 본다: 씬의 빔·화살촉, 도크의 종류 칩, 호버 역할 태그·링,
 * 경로 스트립, 툴팁, Special Link 카드, 2D 관계 지도.
 * 손잡는 관계는 파랑, 다투는 관계는 빨강, 그 밖(공급·투자)은 보라 계열로 읽는다 — 색 하나로 "협력이냐 경쟁이냐" 가 먼저 들어오게 한다.
 * 공급과 투자는 같은 보라 계열이라 톤으로만 구분하고, 정확한 종류는 라벨·툴팁이 말한다.
 */
export const RELATIONSHIP_META: Record<string, RelationshipMeta> = {
  SUPPLY: { code: "SUPPLY", label: "공급", color: "#A06BFF", directed: true, description: "부품·장비·원자재를 공급하는 관계. 화살표 방향으로 충격이 전이됩니다." },
  INVEST: { code: "INVEST", label: "투자·지분", color: "#C6A5FF", directed: true, description: "지분 보유·출자 관계. 지배 방향으로 표시합니다." },
  PARTNER: { code: "PARTNER", label: "협력", color: "#4F8DFF", directed: false, description: "합작·제휴·공동 개발처럼 방향이 없는 협력 관계." },
  COMPETE: { code: "COMPETE", label: "경쟁", color: "#FF4D6D", directed: false, description: "같은 시장에서 점유율·가격을 다투는 관계." },
};
export const RELATIONSHIP_ORDER: RelationshipType[] = ["SUPPLY", "INVEST", "PARTNER", "COMPETE"];
/**
 * 지분 관계로 취급할 유형 코드. relationship_type 시드가 아직 없어 코드가 바뀔 수 있으므로
 * 화면(투자·지분 트리)이 문자열을 직접 비교하지 않고 이 배열만 참조하게 한다.
 */
export const OWNERSHIP_TYPES: RelationshipType[] = ["INVEST"];

/** name 은 서버가 relationship_type.name 을 응답에 실어 주면 하드코딩 라벨 대신 쓰기 위한 자리다. */
export function relationshipMeta(code: RelationshipType, name?: string | null): RelationshipMeta {
  const base =
    RELATIONSHIP_META[code] ??
    ({
      code,
      label: String(code),
      color: "#AAB3C2",
      directed: false,
      description: "정의되지 않은 관계 유형",
    } satisfies RelationshipMeta);
  return name ? { ...base, label: name } : base;
}

/* ------------------------------ 감성 / 영향 방향 ------------------------------ */
export interface ToneMeta {
  label: string;
  color: string;
  fg: string;
  bg: string;
}
export const SENTIMENT_META: Record<Sentiment, ToneMeta> = {
  POSITIVE: { label: "긍정", color: "#2AC769", fg: "#5BE08C", bg: "rgba(42,199,105,.13)" },
  NEGATIVE: { label: "부정", color: "#FF5C7A", fg: "#FF8FA3", bg: "rgba(255,92,122,.14)" },
  NEUTRAL: { label: "중립", color: "#8B95A1", fg: "#AAB3C2", bg: "rgba(255,255,255,.06)" },
};
export const IMPACT_META: Record<ImpactDirection, ToneMeta> = {
  POSITIVE: { label: "긍정 전이", color: "#2AC769", fg: "#5BE08C", bg: "rgba(42,199,105,.13)" },
  NEGATIVE: { label: "부정 전이", color: "#FF5C7A", fg: "#FF8FA3", bg: "rgba(255,92,122,.14)" },
  NEUTRAL: { label: "중립", color: "#8B95A1", fg: "#AAB3C2", bg: "rgba(255,255,255,.06)" },
};
export const SENTIMENTS: Sentiment[] = ["POSITIVE", "NEGATIVE", "NEUTRAL"];
// 예전엔 감성 분석이 아직 없는 기사(sentiment: null)를 SENTIMENT_PENDING("분석 전")으로 따로 표시했으나,
// 제품 결정으로 분석 전 상태도 그냥 중립으로 보여주기로 했다 (components/ui.tsx SentimentBadge 참고).

export function marketLabel(market: Market) {
  if (market === "KOSPI" || market === "KOSDAQ") return "KRX";
  if (market === "NASDAQ") return "NASDAQ";
  return market;
}
/** 시장별 거래 통화. 미국·일본·중국 상장 종목을 원화로 표기하지 않도록 시장 코드를 전부 적는다. */
const MARKET_CURRENCY: Record<string, CurrencyCode> = {
  KOSPI: "KRW",
  KOSDAQ: "KRW",
  NASDAQ: "USD",
  NYSE: "USD",
  AMEX: "USD",
  TSE: "JPY",
  SSE: "CNY",
  SZSE: "CNY",
};
export function marketCurrency(market: Market): CurrencyCode {
  return MARKET_CURRENCY[market] ?? "KRW";
}

/** 점수(0~100)를 라벨로 */
export function scoreBand(score: number) {
  if (score >= 80) return "매우 강함";
  if (score >= 60) return "강함";
  if (score >= 40) return "보통";
  if (score >= 20) return "약함";
  return "미약";
}
