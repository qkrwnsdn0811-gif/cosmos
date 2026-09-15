/**
 * 목업 우주 빌더 — data.ts 의 시드를 API 명세 형태의 데이터셋으로 조립한다.
 * 결정론적 난수를 쓰므로 새로고침해도 같은 결과가 나오고, 스냅샷 ID 는 1시간 단위로 바뀐다.
 */
import type { AltitudeWindow, Candle, ImpactDirection, LatestGraphNode, Market, RelationshipType, Sentiment } from "@/api/types";
import { marketCurrency } from "@/lib/meta";
import { gaussian, hashString, seededRandom, stableUuid } from "@/lib/rng";
import { COMPANY_SEEDS, INDUSTRY_DESCRIPTIONS, LISTED_SHARES, RELATION_SEEDS, type IndustryName } from "./data";

export interface MockIndustry {
  industryId: string;
  parentIndustryId: string | null;
  name: IndustryName;
  description: string;
  companyCount: number;
}
export interface MockCompany {
  companyId: string;
  name: string;
  /** 영문명이 국문명과 같으면 DB 에 따로 담지 않는다고 보고 null 로 둔다 (선택 필드 검증용) */
  nameEn: string | null;
  stockCode: string;
  market: Market;
  industry: IndustryName;
  industryId: string;
  description: string;
  /** 목업 검색이 흉내 내는 company_alias */
  aliases: string[];
  /** company 테이블에 없는 값 — 목업에서만 채워 화면 자리를 검증한다 */
  listedShares: number;
}
export interface MockEdge {
  relationshipId: string;
  sourceCompanyId: string;
  targetCompanyId: string;
  relationshipType: RelationshipType;
  /** 시스템 기본 비율(뉴스 50 · 공시 50)로 계산한 공통 점수 */
  score: number;
  /** 구성 점수 — 근거가 없는 쪽은 null 이다 (둘 다 null 인 관계는 화면에서 빠진다) */
  newsScore: number | null;
  disclosureScore: number | null;
  impactDirection: ImpactDirection;
  confidence: number;
  evidenceCount: number;
  reason: string;
}
export interface MockNewsRelated {
  companyId: string;
  sentiment: Sentiment;
  relevanceScore: number;
  impactScore: number;
}
export interface MockNews {
  newsId: string;
  title: string;
  summary: string;
  publisher: string;
  author: string | null;
  originalUrl: string;
  publishedAt: string;
  publishedMs: number;
  sentiment: Sentiment;
  related: MockNewsRelated[];
  evidence: { sentence: string; confidence: number }[];
  relationshipId: string | null;
  contributionScore: number;
}
export interface MockComment {
  commentId: string;
  companyId: string;
  /** users.user_id 는 BIGSERIAL — 목업도 정수를 쓴다 */
  userId: number;
  nickname: string;
  content: string;
  createdAt: string;
  updatedAt: string;
  edited: boolean;
}

const DAY = 86_400_000;
const HOUR = 3_600_000;

/* ------------------------------------------------------------------ */
/* 산업·기업                                                           */
/* ------------------------------------------------------------------ */
const INDUSTRY_NAMES = Object.keys(INDUSTRY_DESCRIPTIONS) as IndustryName[];

export const INDUSTRIES: MockIndustry[] = INDUSTRY_NAMES.map((name) => ({
  industryId: stableUuid(`industry:${name}`),
  parentIndustryId: null,
  name,
  description: INDUSTRY_DESCRIPTIONS[name],
  companyCount: 0,
}));
const INDUSTRY_BY_NAME = new Map(INDUSTRIES.map((i) => [i.name, i]));

/** 시드에 없는 기업의 상장주식수 — 종목코드 해시로 정해 새로고침해도 같은 값이 나오게 한다 */
function fallbackShares(stockCode: string, market: Market) {
  const r = seededRandom(hashString(`shares:${stockCode}`))();
  const krw = market === "KOSPI" || market === "KOSDAQ";
  const base = krw ? 8_000_000 : 40_000_000;
  const scale = Math.exp(r * 4.2);
  return Math.round((base * scale) / 1000) * 1000;
}

export const COMPANIES: MockCompany[] = COMPANY_SEEDS.map(([name, nameEn, stockCode, market, industry, description, aliases]) => ({
  companyId: stableUuid(`company:${market}:${stockCode}`),
  name,
  nameEn: nameEn === name ? null : nameEn,
  stockCode,
  market,
  industry,
  industryId: INDUSTRY_BY_NAME.get(industry)!.industryId,
  description,
  aliases: aliases ?? [],
  listedShares: LISTED_SHARES[stockCode] ?? fallbackShares(stockCode, market),
}));
COMPANIES.forEach((c) => {
  INDUSTRY_BY_NAME.get(c.industry)!.companyCount += 1;
});
export const COMPANY_BY_ID = new Map(COMPANIES.map((c) => [c.companyId, c]));
const COMPANY_BY_NAME = new Map(COMPANIES.map((c) => [c.name, c]));

/* ------------------------------------------------------------------ */
/* 관계                                                                */
/* ------------------------------------------------------------------ */
const UNDIRECTED: Set<string> = new Set(["COMPETE", "PARTNER"]);
export const isDirected = (type: RelationshipType) => !UNDIRECTED.has(type);

/**
 * 뉴스·공시 구성 점수 — 평균이 공통 점수와 같도록 위아래로 벌린다.
 * 기본 50:50 이면 표시 점수가 score 와 같고, 슬라이더를 움직이면 한쪽으로 기운다.
 * 실제 데이터처럼 한쪽 근거만 있는 관계(≈14%)와 아직 근거가 없는 관계(≈2%)도 섞는다.
 */
function components(score: number, rng: () => number) {
  const r = rng();
  if (r < 0.02) return { newsScore: null, disclosureScore: null };
  const round1 = (v: number) => Math.round(Math.min(100, Math.max(0, v)) * 10) / 10;
  if (r < 0.1) return { newsScore: round1(score), disclosureScore: null };
  if (r < 0.16) return { newsScore: null, disclosureScore: round1(score) };
  // 0~100 밖으로 나가면 평균이 공통 점수와 어긋난다 — 양끝 여유만큼만 벌린다
  const room = Math.min(22, score, 100 - score);
  const spread = (rng() * 2 - 1) * room;
  return { newsScore: round1(score + spread), disclosureScore: round1(score - spread) };
}

function buildEdges(): MockEdge[] {
  const rng = seededRandom(20260908);
  const seen = new Set<string>();
  const edges: MockEdge[] = [];

  const push = (a: MockCompany, b: MockCompany, type: RelationshipType, score: number, impact?: ImpactDirection, reason = "") => {
    if (a.companyId === b.companyId || score <= 0) return;
    let s = a;
    let t = b;
    if (!isDirected(type) && s.companyId > t.companyId) [s, t] = [t, s];
    const key = `${s.companyId}|${t.companyId}|${type}`;
    if (seen.has(key)) return;
    seen.add(key);
    const finalImpact: ImpactDirection =
      impact ?? (type === "COMPETE" ? (rng() < 0.4 ? "NEGATIVE" : "NEUTRAL") : rng() < 0.6 ? "POSITIVE" : "NEUTRAL");
    const confidence = Math.min(0.98, Math.max(0.4, 0.5 + (score / 100) * 0.42 + gaussian(rng) * 0.05));
    const evidenceCount = Math.max(1, Math.round(2 + (score / 100) * 28 + gaussian(rng) * 3));
    edges.push({
      relationshipId: stableUuid(`rel:${key}`),
      sourceCompanyId: s.companyId,
      targetCompanyId: t.companyId,
      relationshipType: type,
      score: Math.round(score * 10) / 10,
      ...components(score, rng),
      impactDirection: finalImpact,
      confidence: Math.round(confidence * 100) / 100,
      evidenceCount,
      reason,
    });
  };

  RELATION_SEEDS.forEach(([a, b, type, score, impact, reason]) => {
    const ca = COMPANY_BY_NAME.get(a);
    const cb = COMPANY_BY_NAME.get(b);
    if (!ca || !cb) return;
    push(ca, cb, type, score, impact, reason);
  });

  // 고립 노드 보정: 같은 산업 내 경쟁·협력 관계를 하나 이상 부여한다
  const degree = new Map<string, number>();
  edges.forEach((e) => {
    degree.set(e.sourceCompanyId, (degree.get(e.sourceCompanyId) ?? 0) + 1);
    degree.set(e.targetCompanyId, (degree.get(e.targetCompanyId) ?? 0) + 1);
  });
  COMPANIES.forEach((c) => {
    const d = degree.get(c.companyId) ?? 0;
    if (d >= 2) return;
    const peers = COMPANIES.filter((p) => p.industry === c.industry && p.companyId !== c.companyId);
    const need = 2 - d;
    for (let i = 0; i < need && peers.length; i += 1) {
      const p = peers[Math.floor(rng() * peers.length)];
      const type: RelationshipType = rng() < 0.65 ? "COMPETE" : "PARTNER";
      push(c, p, type, 22 + rng() * 28, undefined, "동일 산업 노출");
    }
  });
  return edges;
}

export const EDGES: MockEdge[] = buildEdges();
export const EDGE_BY_ID = new Map(EDGES.map((e) => [e.relationshipId, e]));

/** 기업별 인접 간선 (점수 내림차순) */
export const ADJACENCY = new Map<string, MockEdge[]>();
EDGES.forEach((e) => {
  for (const id of [e.sourceCompanyId, e.targetCompanyId]) {
    if (!ADJACENCY.has(id)) ADJACENCY.set(id, []);
    ADJACENCY.get(id)!.push(e);
  }
});
ADJACENCY.forEach((list) => list.sort((a, b) => b.score - a.score));

export function otherEnd(e: MockEdge, id: string) {
  return e.sourceCompanyId === id ? e.targetCompanyId : e.sourceCompanyId;
}

/* ------------------------------------------------------------------ */
/* 스냅샷 — 1시간 단위로 교체                                             */
/* ------------------------------------------------------------------ */
export function currentSnapshot(now = Date.now()) {
  const hour = Math.floor(now / HOUR);
  return {
    snapshotId: stableUuid(`snapshot:${hour}`),
    asOfAt: new Date(hour * HOUR).toISOString(),
    nextRefreshAt: new Date((hour + 1) * HOUR).toISOString(),
  };
}

/* ------------------------------------------------------------------ */
/* 뉴스                                                                */
/* ------------------------------------------------------------------ */
const KR_PUBLISHERS = ["연합뉴스", "한국경제", "매일경제", "서울경제", "전자신문", "비즈니스포스트", "더벨", "조선비즈", "머니투데이", "이데일리", "뉴스1", "아시아경제"];
const US_PUBLISHERS = ["Reuters", "Bloomberg", "CNBC", "The Information", "WSJ", "Financial Times", "TechCrunch", "Nikkei Asia"];
const AUTHORS = ["김산업", "이반도", "박전지", "최우주", "정성운", "한데이터", null, "오분석", "윤그래프", null];

type Tmpl = { t: string; s: string; e: string; tone: Sentiment };
const TEMPLATES: Record<string, Tmpl[]> = {
  SUPPLY: [
    { t: "{A}, {B}에 {R} 물량 확대… \"내년까지 장기 계약\"", s: "{A}가 {B}와 {R} 관련 공급 계약을 연장하며 물량을 늘리기로 했다.", e: "{A}는 {B}에 공급하는 {R} 물량을 단계적으로 확대한다.", tone: "POSITIVE" },
    { t: "{B}, {A} 의존도 낮추나… 공급망 다변화 검토", s: "{B}가 {A}로부터 조달하는 {R} 비중을 줄이기 위해 대체 공급사를 검토하는 것으로 알려졔다.", e: "{B}는 {R} 공급선을 다변화하는 방안을 내부적으로 검토하고 있다.", tone: "NEGATIVE" },
    { t: "{A}·{B} {R} 공급 협상 막바지… 단가 인상 여부 관건", s: "{A}와 {B}가 {R} 공급 단가를 놓고 협상을 이어가고 있다.", e: "양사는 {R} 공급 조건을 놓고 협상을 진행 중이다.", tone: "NEUTRAL" },
    { t: "\"{B} 증설 수혜\"… {A} {R} 수주 기대감", s: "{B}의 설비 투자 확대로 {A}의 {R} 수주가 늘어날 것이라는 전망이 나온다.", e: "{B}의 투자 확대는 {A}의 {R} 매출에 직접적인 영향을 준다.", tone: "POSITIVE" },
    { t: "{A}, {B} 품질 인증 통과… {R} 본격 양산", s: "{A}가 {B}의 품질 인증을 통과해 {R} 양산에 들어간다.", e: "{A}는 {B} 인증을 완료하고 {R} 양산 체제에 돌입했다.", tone: "POSITIVE" },
    { t: "{B} 생산 차질에 {A} 실적 우려… {R} 출하 지연", s: "{B}의 생산 일정 지연으로 {A}의 {R} 출하가 늦춰질 것으로 보인다.", e: "{B}의 일정 지연은 {A}의 {R} 출하 시점에 영향을 미친다.", tone: "NEGATIVE" },
  ],
  INVEST: [
    { t: "{A}, {B} 지분 추가 취득… 지배력 강화", s: "{A}가 {B} 지분을 추가로 사들이며 지배력을 높였다. {R} 수준이다.", e: "{A}는 {B} 지분을 추가 취득했다고 공시했다.", tone: "POSITIVE" },
    { t: "{B} 실적 부진에 {A} 지분법 손실 반영", s: "{B}의 부진이 {A}의 연결 실적에 지분법 손실로 반영됐다.", e: "{A}는 {B} 관련 지분법 손실을 인식했다.", tone: "NEGATIVE" },
    { t: "{A}·{B} 지배구조 재편 시나리오 부상", s: "{A}가 보유한 {B} 지분({R})을 둘러싼 지배구조 재편 가능성이 거론된다.", e: "시장에서는 {A}의 {B} 지분 활용 방안을 주목하고 있다.", tone: "NEUTRAL" },
    { t: "{B} 배당 확대… 최대주주 {A} 현금 유입 기대", s: "{B}가 배당을 늘리면서 최대주주 {A}의 배당 수익이 증가할 전망이다.", e: "{B}의 배당 확대는 {A}의 현금흐름에 긍정적이다.", tone: "POSITIVE" },
  ],
  PARTNER: [
    { t: "{A}·{B} {R} 손잡다… 공동 프로젝트 착수", s: "{A}와 {B}가 {R}을 위한 협력 계약을 체결했다.", e: "양사는 {R} 협력을 공식화했다.", tone: "POSITIVE" },
    { t: "{A}-{B} 협력 삐끗?… {R} 일정 재조정", s: "{A}와 {B}가 추진하던 {R} 일정이 재조정되며 우려가 제기된다.", e: "{R} 일정이 당초 계획보다 늦춰졌다.", tone: "NEGATIVE" },
    { t: "{A}, {B}와 {R} 범위 확대 논의", s: "{A}와 {B}가 {R}의 범위를 넓히는 방안을 논의하고 있다.", e: "양사는 {R} 확대 방안을 협의 중이다.", tone: "NEUTRAL" },
    { t: "\"{R} 성과 가시화\"… {A}·{B} 협력 2년 만에 첫 결실", s: "{A}와 {B}의 {R} 협력이 첫 성과를 냈다.", e: "{R} 협력의 첫 결과물이 공개됐다.", tone: "POSITIVE" },
  ],
  COMPETE: [
    { t: "{A} vs {B}, {R} 격화… 점유율 뺏기 본격화", s: "{A}와 {B}의 {R}이 격화되며 점유율 변동이 예상된다.", e: "{A}와 {B}는 {R} 국면에서 정면 충돌하고 있다.", tone: "NEGATIVE" },
    { t: "{B} 신제품 공개에 {A} 긴장… {R} 새 국면", s: "{B}가 신제품을 공개하며 {A}와의 {R}이 새 국면을 맞았다.", e: "{B}의 신제품은 {A}를 직접 겨냥한 것으로 평가된다.", tone: "NEUTRAL" },
    { t: "{A}, {B} 제치고 1위… {R}서 우위", s: "{A}가 {B}를 제치고 시장 1위에 올랐다.", e: "{A}는 {R}에서 {B}를 앞섰다.", tone: "POSITIVE" },
    { t: "가격 인하 경쟁… {A}·{B} 수익성 동반 압박", s: "{A}와 {B}의 가격 경쟁이 심화되며 양사 수익성이 압박받고 있다.", e: "{R}에 따른 가격 인하가 양사 마진을 압박한다.", tone: "NEGATIVE" },
    { t: "업황 회복 기대… {A}·{B} 동반 강세", s: "업황 회복 기대감에 {A}와 {B}가 동반 강세를 보였다.", e: "{A}와 {B}는 같은 업황 사이클에 노출돼 있다.", tone: "POSITIVE" },
  ],
  SOLO: [
    { t: "{A}, 분기 실적 시장 기대치 상회", s: "{A}가 발표한 분기 실적이 시장 예상을 웃돌았다.", e: "{A}의 영업이익은 컨센서스를 상회했다.", tone: "POSITIVE" },
    { t: "{A}, 신규 투자 계획 발표… \"{D}\" 강화", s: "{A}가 {D} 분야에 대한 투자 계획을 내놨다.", e: "{A}는 {D} 관련 투자를 확대한다.", tone: "POSITIVE" },
    { t: "{A} 주가 약세… 규제 리스크 부각", s: "{A}가 규제 이슈로 약세를 보였다.", e: "규제 변수가 {A}의 단기 실적에 부담이 될 수 있다.", tone: "NEGATIVE" },
    { t: "{A}, 경영진 교체… 전략 변화 예고", s: "{A}가 주요 경영진을 교체하며 전략 변화를 예고했다.", e: "{A}의 경영진 교체는 사업 방향 전환 신호로 해석된다.", tone: "NEUTRAL" },
    { t: "{A} 목표주가 상향 잇따라", s: "증권사들이 {A} 목표주가를 상향 조정했다.", e: "{A}에 대한 증권사 전망이 개선됐다.", tone: "POSITIVE" },
    { t: "{A}, 해외 수요 둔화에 재고 부담", s: "{A}가 해외 수요 둔화로 재고 부담을 안고 있다.", e: "{A}의 재고 자산이 전분기 대비 증가했다.", tone: "NEGATIVE" },
  ],
};

function fill(t: string, a: MockCompany, b: MockCompany | null, reason: string) {
  return t
    .replaceAll("{A}", a.name)
    .replaceAll("{B}", b?.name ?? "")
    .replaceAll("{R}", reason || (b ? "협력" : "사업"))
    .replaceAll("{D}", a.description.split("·")[0]);
}

function slug(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "") || "press";
}

function buildNews(): MockNews[] {
  const rng = seededRandom(777);
  const now = Date.now();
  const list: MockNews[] = [];
  let seq = 0;

  const pickTone = (impact: ImpactDirection): Sentiment => {
    const r = rng();
    if (impact === "POSITIVE") return r < 0.62 ? "POSITIVE" : r < 0.9 ? "NEUTRAL" : "NEGATIVE";
    if (impact === "NEGATIVE") return r < 0.58 ? "NEGATIVE" : r < 0.88 ? "NEUTRAL" : "POSITIVE";
    return r < 0.45 ? "NEUTRAL" : r < 0.75 ? "POSITIVE" : "NEGATIVE";
  };

  const make = (a: MockCompany, b: MockCompany | null, tmpl: Tmpl, reason: string, edge: MockEdge | null) => {
    seq += 1;
    const ageDays = Math.pow(rng(), 1.6) * 45; // 최근에 몰리게
    const publishedMs = now - ageDays * DAY - rng() * 6 * HOUR;
    const isKr = a.market === "KOSPI" || a.market === "KOSDAQ" || (b && (b.market === "KOSPI" || b.market === "KOSDAQ"));
    const pubs = isKr ? KR_PUBLISHERS : US_PUBLISHERS;
    const publisher = pubs[Math.floor(rng() * pubs.length)];
    const newsId = stableUuid(`news:${seq}:${a.companyId}:${b?.companyId ?? ""}`);
    const related: MockNewsRelated[] = [];
    const base = edge ? edge.score / 100 : 0.5;
    related.push({ companyId: a.companyId, sentiment: tmpl.tone, relevanceScore: round2(0.72 + rng() * 0.26), impactScore: round2(0.35 + base * 0.5 + rng() * 0.12) });
    if (b) related.push({ companyId: b.companyId, sentiment: pickTone(edge?.impactDirection ?? "NEUTRAL"), relevanceScore: round2(0.55 + rng() * 0.4), impactScore: round2(0.3 + base * 0.45 + rng() * 0.15) });
    // 가끔 이웃 기업 1개 추가 언급
    if (b && rng() < 0.28) {
      const adj = ADJACENCY.get(a.companyId) ?? [];
      const n = adj[Math.floor(rng() * Math.min(adj.length, 4))];
      if (n) {
        const nid = otherEnd(n, a.companyId);
        if (nid !== b.companyId) related.push({ companyId: nid, sentiment: "NEUTRAL", relevanceScore: round2(0.3 + rng() * 0.3), impactScore: round2(0.15 + rng() * 0.3) });
      }
    }
    const evidence = [{ sentence: fill(tmpl.e, a, b, reason), confidence: round2(0.72 + rng() * 0.26) }];
    if (rng() < 0.5) evidence.push({ sentence: fill(tmpl.s, a, b, reason), confidence: round2(0.6 + rng() * 0.3) });
    list.push({
      newsId,
      title: fill(tmpl.t, a, b, reason),
      summary: fill(tmpl.s, a, b, reason),
      publisher,
      author: isKr ? AUTHORS[Math.floor(rng() * AUTHORS.length)] : null,
      originalUrl: `https://news.example.com/${slug(publisher)}/${newsId.slice(0, 8)}`,
      publishedAt: new Date(publishedMs).toISOString(),
      publishedMs,
      sentiment: tmpl.tone,
      related,
      evidence,
      relationshipId: edge?.relationshipId ?? null,
      contributionScore: edge ? round2(0.3 + (edge.score / 100) * 0.55 + rng() * 0.12) : 0,
    });
  };

  EDGES.forEach((e) => {
    const a = COMPANY_BY_ID.get(e.sourceCompanyId)!;
    const b = COMPANY_BY_ID.get(e.targetCompanyId)!;
    const n = Math.max(1, Math.min(6, Math.round(e.evidenceCount / 5)));
    const pool = TEMPLATES[String(e.relationshipType)] ?? TEMPLATES.PARTNER;
    for (let i = 0; i < n; i += 1) {
      // 영향 방향에 맞는 톤을 우선 고르되 일부는 뒤섞는다
      const wanted = pickTone(e.impactDirection);
      const candidates = pool.filter((t) => t.tone === wanted);
      const source = candidates.length && rng() < 0.8 ? candidates : pool;
      const tmpl = source[Math.floor(rng() * source.length)];
      // source/target 순서를 가끔 뒤집어 다양성 확보 (무방향 관계만)
      const flip = !isDirected(e.relationshipType) && rng() < 0.5;
      make(flip ? b : a, flip ? a : b, tmpl, e.reason, e);
    }
  });
  COMPANIES.forEach((c) => {
    const n = 1 + Math.floor(rng() * 3);
    for (let i = 0; i < n; i += 1) {
      const tmpl = TEMPLATES.SOLO[Math.floor(rng() * TEMPLATES.SOLO.length)];
      make(c, null, tmpl, "", null);
    }
  });

  list.sort((x, y) => y.publishedMs - x.publishedMs);
  return list;
}
function round2(n: number) {
  return Math.round(n * 100) / 100;
}

export const NEWS: MockNews[] = buildNews();
export const NEWS_BY_ID = new Map(NEWS.map((n) => [n.newsId, n]));
export const NEWS_BY_COMPANY = new Map<string, MockNews[]>();
NEWS.forEach((n) => {
  n.related.forEach((r) => {
    if (!NEWS_BY_COMPANY.has(r.companyId)) NEWS_BY_COMPANY.set(r.companyId, []);
    NEWS_BY_COMPANY.get(r.companyId)!.push(n);
  });
});

/* ------------------------------------------------------------------ */
/* 주가 — 결정론적 GBM (영업일 1Y)                                        */
/* ------------------------------------------------------------------ */
const priceCache = new Map<string, Candle[]>();
export function stockSeries(company: MockCompany): Candle[] {
  const hit = priceCache.get(company.companyId);
  if (hit) return hit;
  const rng = seededRandom(hashString(`price:${company.companyId}`));
  const krw = company.market === "KOSPI" || company.market === "KOSDAQ";
  const jpy = company.market === "TSE";
  const cny = company.market === "SZSE";
  let price = krw ? Math.exp(9 + rng() * 4.4) : jpy ? 800 + rng() * 3000 : cny ? 80 + rng() * 240 : Math.exp(3.2 + rng() * 3.2);
  const drift = (rng() - 0.45) * 0.0012;
  const vol = 0.012 + rng() * 0.02;
  const out: Candle[] = [];
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);
  const days: Date[] = [];
  const cursor = new Date(end);
  while (days.length < 262) {
    const dow = cursor.getUTCDay();
    if (dow !== 0 && dow !== 6) days.push(new Date(cursor));
    cursor.setUTCDate(cursor.getUTCDate() - 1);
  }
  days.reverse();
  for (const d of days) {
    const ret = drift + gaussian(rng) * vol;
    const open = price;
    const close = Math.max(1, open * Math.exp(ret));
    const hi = Math.max(open, close) * (1 + Math.abs(gaussian(rng)) * vol * 0.6);
    const lo = Math.min(open, close) * (1 - Math.abs(gaussian(rng)) * vol * 0.6);
    const tick = krw ? (close > 100000 ? 100 : close > 10000 ? 10 : 1) : 0.01;
    const q = (v: number) => Math.round(v / tick) * tick;
    out.push({
      tradingAt: d.toISOString(),
      openPrice: q(open),
      highPrice: q(hi),
      lowPrice: q(lo),
      closePrice: q(close),
      tradingVolume: Math.round((krw ? 200_000 : 2_000_000) * Math.exp(gaussian(rng) * 0.6 + 1)),
    });
    price = close;
  }
  priceCache.set(company.companyId, out);
  return out;
}

/**
 * 등락률 기간 → 기준 종가를 고를 때 되짚을 캔들 수.
 * 주가 탭은 mockApi.stockPrices 가 준 items = all.slice(-n) 의 첫 캔들(= all[L-n])을 기준으로 삼는다.
 * 은하 뷰 고도와 주가 탭 등락률이 어긋나지 않도록 같은 n(1M=22·3M=65)을 그대로 되짚는다.
 * 1D 는 주가 탭에 대응 기간이 없어 직전 거래일(캔들 2개)로 둔다.
 */
const CHANGE_BACK: Record<AltitudeWindow, number> = { "1D": 2, "1M": 22, "3M": 65 };

/**
 * 기간별 등락률(퍼센트) — 마지막 종가 대비 기준일 종가.
 * stockSeries 가 시드 난수라 같은 기업이면 늘 같은 값이고, 구간을 채울 만큼 시계열이 없으면 null 이다.
 */
export function priceChanges(company: MockCompany): NonNullable<LatestGraphNode["priceChange"]> {
  const s = stockSeries(company);
  const last = s.at(-1);
  const out: NonNullable<LatestGraphNode["priceChange"]> = {};
  (Object.keys(CHANGE_BACK) as AltitudeWindow[]).forEach((w) => {
    const prev = s[s.length - CHANGE_BACK[w]];
    out[w] = last && prev && prev.closePrice > 0 ? round2(((last.closePrice - prev.closePrice) / prev.closePrice) * 100) : null;
  });
  return out;
}

/** 최근 종가 × 상장주식수. company 테이블에 컬럼이 생기면 서버 값으로 대체된다. */
export function marketCapOf(company: MockCompany) {
  const last = stockSeries(company).at(-1);
  if (!last) return null;
  return Math.round(last.closePrice * company.listedShares);
}

/** 시장 통화 → 원화 환산율(대략치). 은하 뷰의 행성 크기가 시장이 다른 기업의 시가총액을 한 척도로 비교할 수 있게만 하면 된다 */
const KRW_PER: Record<string, number> = { KRW: 1, USD: 1350, JPY: 9, CNY: 190 };

/** 원화 환산 시가총액 — 그래프 노드(LatestGraphNode.marketCapKrw)에 싣는 값. 실 서버는 환산까지 해서 내려 준다고 가정한다 */
export function marketCapKrwOf(company: MockCompany) {
  const cap = marketCapOf(company);
  if (cap === null) return null;
  return Math.round(cap * (KRW_PER[marketCurrency(company.market)] ?? 1));
}

/* ------------------------------------------------------------------ */
/* 커뮤니티 댓글                                                          */
/* ------------------------------------------------------------------ */
/** 시드 댓글 작성자 id — 실제 가입 사용자(1부터 증가)와 겹치지 않게 큰 수에서 시작한다 */
const SEED_USER_ID_BASE = 1000;
const NICKS = ["orbit_660", "graph_reader", "ner_lab", "tsv_watch", "성운관측자", "hbm_holder", "배터리덕후", "밸류체인", "공시읽는사람", "장기투자자K", "퀀트지망생", "supplychain_kim"];
const COMMENT_TMPL = [
  "{A} 관련 뉴스가 최근에 확 늘었네요. 관계 점수도 같이 오르는지 지켜보고 있습니다.",
  "{A}의 {B} 노출이 생각보다 크네요. 공급망 쪽 근거 기사가 더 붙으면 좋겠습니다.",
  "관세 이슈가 {A}에 어떤 경로로 전이되는지 이 그래프로 보니 이해가 쉽습니다.",
  "{A} 지분 구조 정리해주신 분 계신가요? 투자·지분 간선 방향이 헷갈립니다.",
  "{A}·{B} 간선이 30D 기준으로는 강한데 7D로 보면 약해지네요. 최근 뉴스가 줄어든 듯.",
  "근거 문장 보면 {A} 쪽은 대부분 긍정인데 상대 기업은 중립이 많아요. 비대칭이 흥미롭습니다.",
  "{A} 실적 발표 이후 관계 점수 변화가 궤적으로 남으면 좋겠어요.",
  "{A} 경쟁 간선은 점수보다 뉴스 톤이 더 중요해 보입니다. 부정 전이 표시가 도움이 되네요.",
];

function buildComments(): MockComment[] {
  const rng = seededRandom(4321);
  const out: MockComment[] = [];
  const now = Date.now();
  COMPANIES.forEach((c) => {
    const deg = ADJACENCY.get(c.companyId)?.length ?? 0;
    const n = Math.min(8, Math.floor(deg / 2 + rng() * 2));
    for (let i = 0; i < n; i += 1) {
      const adj = ADJACENCY.get(c.companyId) ?? [];
      const nb = adj.length ? COMPANY_BY_ID.get(otherEnd(adj[Math.floor(rng() * adj.length)], c.companyId)) : null;
      const nickIdx = Math.floor(rng() * NICKS.length);
      const created = now - rng() * 12 * DAY;
      const edited = rng() < 0.12;
      out.push({
        commentId: stableUuid(`comment:${c.companyId}:${i}`),
        companyId: c.companyId,
        userId: SEED_USER_ID_BASE + nickIdx,
        nickname: NICKS[nickIdx],
        content: fill(COMMENT_TMPL[Math.floor(rng() * COMMENT_TMPL.length)], c, nb ?? null, ""),
        createdAt: new Date(created).toISOString(),
        updatedAt: new Date(edited ? created + rng() * HOUR : created).toISOString(),
        edited,
      });
    }
  });
  out.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return out;
}
export const SEED_COMMENTS: MockComment[] = buildComments();

/* ------------------------------------------------------------------ */
/* 지표                                                                */
/* ------------------------------------------------------------------ */
export function windowDays(w: "7D" | "30D" | "90D") {
  return w === "7D" ? 7 : w === "30D" ? 30 : 90;
}

export function metricsFor(companyId: string, w: "7D" | "30D" | "90D", until = Date.now()) {
  const from = until - windowDays(w) * DAY;
  const items = (NEWS_BY_COMPANY.get(companyId) ?? []).filter((n) => n.publishedMs <= until && n.publishedMs > from);
  let pos = 0;
  let neg = 0;
  items.forEach((n) => {
    const r = n.related.find((x) => x.companyId === companyId);
    if (r?.sentiment === "POSITIVE") pos += 1;
    else if (r?.sentiment === "NEGATIVE") neg += 1;
  });
  const total = items.length;
  const neu = total - pos - neg;
  return {
    newsMentionCount: total,
    positiveCount: pos,
    negativeCount: neg,
    sentimentScore: total ? round2((pos + neu * 0.5) / total) : null,
    relationshipCount: ADJACENCY.get(companyId)?.length ?? 0,
  };
}
