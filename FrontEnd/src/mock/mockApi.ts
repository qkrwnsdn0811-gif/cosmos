/**
 * 브라우저 안에서 동작하는 API 목업. 응답 형태는 docs/api-specification.md 와 동일하다.
 * 로그인·관심 기업·스크랩·댓글은 localStorage 에 저장되어 새로고침 후에도 유지된다.
 * 뉴스·공시 비율은 서버 설정이 아니라 화면 설정이라 여기서 다루지 않는다 (store/weight).
 */
import type { CosmosApi } from "@/api/contract";
import { ApiError, tokenStore } from "@/api/client";
import type * as T from "@/api/types";
import {
  ADJACENCY,
  COMPANIES,
  COMPANY_BY_ID,
  EDGES,
  EDGE_BY_ID,
  INDUSTRIES,
  NEWS,
  NEWS_BY_COMPANY,
  NEWS_BY_ID,
  SEED_COMMENTS,
  currentSnapshot,
  isDirected,
  marketCapKrwOf,
  marketCapOf,
  metricsFor,
  otherEnd,
  priceChanges,
  stockSeries,
  windowDays,
  type MockComment,
  type MockCompany,
  type MockEdge,
  type MockNews,
} from "./universe";

const DAY = 86_400_000;

/* ------------------------------------------------------------------ */
/* 유틸                                                                */
/* ------------------------------------------------------------------ */
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const latency = (base = 140) => sleep(base + Math.random() * 160);

function fail(status: number, code: string, message: string, fieldErrors: T.FieldError[] = []): never {
  throw new ApiError(status, { code, message, fieldErrors });
}

function decodeCursor(cursor?: string) {
  if (!cursor) return 0;
  const n = Number(atob(cursor));
  if (!Number.isInteger(n) || n < 0) fail(400, "INVALID_CURSOR", "커서 형식이 올바르지 않습니다.");
  return n;
}
function paginate<T>(all: T[], params?: { cursor?: string; size?: number }, max = 100): T.Page<T> {
  const size = Math.min(max, Math.max(1, params?.size ?? 20));
  const offset = decodeCursor(params?.cursor);
  const items = all.slice(offset, offset + size);
  const next = offset + size;
  return { items, nextCursor: next < all.length ? btoa(String(next)) : null, hasNext: next < all.length };
}

/* ------------------------------------------------------------------ */
/* 저장소 (localStorage)                                                */
/* ------------------------------------------------------------------ */
interface StoredUser {
  /** users.user_id 는 BIGSERIAL 정수다 */
  userId: number;
  email: string;
  password: string;
  nickname: string;
}
interface UserState {
  watch: Record<string, string>;
  scraps: Record<string, string>;
}
interface StoredComment extends MockComment {
  deleted?: boolean;
}

/** userId 가 UUID 문자열에서 정수로 바뀌어 이전 저장분과 호환되지 않으므로 키 네임스페이스를 올린다 */
const LS = {
  users: "cosmos.mock.v2.users",
  session: "cosmos.mock.v2.session",
  user: (id: number) => `cosmos.mock.v2.user.${id}`,
  comments: "cosmos.mock.v2.comments",
};

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

const DEMO_USER: StoredUser = { userId: 1, email: "demo@cosmos.dev", password: "cosmos123", nickname: "우주탐험가" };
function users(): StoredUser[] {
  const list = read<StoredUser[]>(LS.users, []);
  if (!list.some((u) => u.userId === DEMO_USER.userId)) {
    list.unshift(DEMO_USER);
    write(LS.users, list);
  }
  return list;
}
function userState(userId: number): UserState {
  return read<UserState>(LS.user(userId), { watch: {}, scraps: {} });
}
function saveUserState(userId: number, s: UserState) {
  write(LS.user(userId), s);
}

/** 토큰 → 사용자. 형식: mock.<userId>.<issuedAt> */
function currentUser(): StoredUser | null {
  const token = tokenStore.get();
  if (!token?.startsWith("mock.")) return null;
  const userId = Number(token.split(".")[1]);
  if (!Number.isInteger(userId)) return null;
  return users().find((u) => u.userId === userId) ?? null;
}
function requireUser(): StoredUser {
  const u = currentUser();
  if (!u) fail(401, tokenStore.get() ? "TOKEN_INVALID" : "TOKEN_EXPIRED", "로그인이 필요합니다.");
  return u;
}
function issueToken(userId: number) {
  const token = `mock.${userId}.${Date.now()}`;
  tokenStore.set(token);
  return token;
}

function allComments(): StoredComment[] {
  const extra = read<StoredComment[]>(LS.comments, []);
  const overridden = new Map(extra.map((c) => [c.commentId, c]));
  const merged: StoredComment[] = [];
  SEED_COMMENTS.forEach((c) => merged.push(overridden.get(c.commentId) ?? c));
  extra.forEach((c) => {
    if (!SEED_COMMENTS.some((s) => s.commentId === c.commentId)) merged.push(c);
  });
  return merged.filter((c) => !c.deleted).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}
function upsertComment(c: StoredComment) {
  const extra = read<StoredComment[]>(LS.comments, []);
  const idx = extra.findIndex((x) => x.commentId === c.commentId);
  if (idx >= 0) extra[idx] = c;
  else extra.push(c);
  write(LS.comments, extra);
}

/* ------------------------------------------------------------------ */
/* 매퍼                                                                */
/* ------------------------------------------------------------------ */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * 관계 점수는 서버에서 개인화하지 않는다 — 공통 점수와 구성 점수를 그대로 내려주고,
 * 뉴스·공시 비율은 프론트가 화면에서만 섞는다 (명세 graph '관계 점수 표시 규칙').
 */
function toEdge(e: MockEdge): T.GraphEdge {
  return {
    relationshipId: e.relationshipId,
    sourceCompanyId: e.sourceCompanyId,
    targetCompanyId: e.targetCompanyId,
    relationshipType: e.relationshipType,
    score: e.score,
    newsScore: e.newsScore,
    disclosureScore: e.disclosureScore,
    impactDirection: e.impactDirection,
  };
}
function toSummary(c: MockCompany, watch: Record<string, string>): T.CompanySummary {
  return {
    companyId: c.companyId,
    name: c.name,
    nameEn: c.nameEn,
    stockCode: c.stockCode,
    market: c.market,
    primaryIndustry: { industryId: c.industryId, name: c.industry },
    watched: Boolean(watch[c.companyId]),
  };
}
function toNewsItem(n: MockNews, scraps: Record<string, string>): T.NewsItem {
  return {
    newsId: n.newsId,
    title: n.title,
    summary: n.summary,
    publisher: n.publisher,
    originalUrl: n.originalUrl,
    publishedAt: n.publishedAt,
    sentiment: n.sentiment,
    relatedCompanies: n.related.map((r) => ({ companyId: r.companyId, name: COMPANY_BY_ID.get(r.companyId)?.name ?? "" })),
    scrapped: Boolean(scraps[n.newsId]),
  };
}

/* ------------------------------------------------------------------ */
/* 구현                                                                */
/* ------------------------------------------------------------------ */
export const mockApi: CosmosApi = {
  auth: {
    async checkEmail(email) {
      await latency(80);
      if (!EMAIL_RE.test(email)) fail(400, "VALIDATION_FAILED", "이메일 형식이 올바르지 않습니다.", [{ field: "email", message: "이메일 형식 오류" }]);
      return { available: !users().some((u) => u.email.toLowerCase() === email.toLowerCase()) };
    },
    async checkNickname(nickname) {
      await latency(80);
      if (nickname.length < 2 || nickname.length > 30) fail(400, "VALIDATION_FAILED", "닉네임은 2~30자여야 합니다.", [{ field: "nickname", message: "길이 오류" }]);
      return { available: !users().some((u) => u.nickname === nickname) };
    },
    async login({ email, password }) {
      await latency(220);
      const u = users().find((x) => x.email.toLowerCase() === email.toLowerCase() && x.password === password);
      if (!u) fail(401, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다.");
      write(LS.session, u.userId);
      return { accessToken: issueToken(u.userId) };
    },
    async logout() {
      await latency(60);
      localStorage.removeItem(LS.session);
      tokenStore.set(null);
    },
    async refresh() {
      await latency(90);
      const sessionUser = read<number | null>(LS.session, null);
      if (!sessionUser || !users().some((u) => u.userId === sessionUser)) fail(401, "TOKEN_EXPIRED", "세션이 만료되었습니다.");
      return { accessToken: issueToken(sessionUser) };
    },
    async signup({ email, password, nickname }) {
      await latency(260);
      const fieldErrors: T.FieldError[] = [];
      if (!EMAIL_RE.test(email) || email.length > 255) fieldErrors.push({ field: "email", message: "이메일 형식이 올바르지 않습니다." });
      if (!/^(?=.*[A-Za-z])(?=.*\d).{8,64}$/.test(password)) fieldErrors.push({ field: "password", message: "8~64자, 영문과 숫자를 각 1자 이상 포함해야 합니다." });
      if (nickname.trim().length < 2 || nickname.length > 30) fieldErrors.push({ field: "nickname", message: "닉네임은 2~30자여야 합니다." });
      if (fieldErrors.length) fail(400, "VALIDATION_FAILED", "입력값을 확인해 주세요.", fieldErrors);
      const list = users();
      if (list.some((u) => u.email.toLowerCase() === email.toLowerCase())) fail(409, "EMAIL_DUPLICATED", "이미 사용 중인 이메일입니다.");
      if (list.some((u) => u.nickname === nickname)) fail(409, "NICKNAME_DUPLICATED", "이미 사용 중인 닉네임입니다.");
      // BIGSERIAL 처럼 1부터 증가하는 정수를 발급한다 (DEMO_USER 가 1)
      const nextId = list.reduce((mx, u) => Math.max(mx, u.userId), 0) + 1;
      // 백엔드(UserService)가 이메일을 소문자로 정규화해 저장하므로 목업도 같게 맞춘다 — 헤더·내 페이지 표기가 실서버와 어긋나지 않게
      const user: StoredUser = { userId: nextId, email: email.trim().toLowerCase(), password, nickname: nickname.trim() };
      list.push(user);
      write(LS.users, list);
      return { userId: user.userId, email: user.email, nickname: user.nickname };
    },
  },

  users: {
    async me() {
      await latency(70);
      const u = requireUser();
      return { userId: u.userId, email: u.email, nickname: u.nickname };
    },
    async updateNickname(nickname) {
      await latency(150);
      const u = requireUser();
      if (nickname.trim().length < 2 || nickname.length > 30) fail(400, "VALIDATION_FAILED", "닉네임은 2~30자여야 합니다.", [{ field: "nickname", message: "길이 오류" }]);
      const list = users();
      if (list.some((x) => x.nickname === nickname && x.userId !== u.userId)) fail(409, "NICKNAME_DUPLICATED", "이미 사용 중인 닉네임입니다.");
      const me = list.find((x) => x.userId === u.userId)!;
      me.nickname = nickname.trim();
      write(LS.users, list);
      return { userId: me.userId, email: me.email, nickname: me.nickname };
    },
    async scraps(p) {
      await latency();
      const u = requireUser();
      const s = userState(u.userId);
      const items = Object.entries(s.scraps)
        .map(([newsId, scrappedAt]) => ({ n: NEWS_BY_ID.get(newsId), scrappedAt }))
        .filter((x): x is { n: MockNews; scrappedAt: string } => Boolean(x.n))
        .sort((a, b) => b.scrappedAt.localeCompare(a.scrappedAt))
        .map(({ n, scrappedAt }) => ({ newsId: n.newsId, title: n.title, summary: n.summary, publisher: n.publisher, originalUrl: n.originalUrl, publishedAt: n.publishedAt, scrappedAt }));
      return paginate(items, p);
    },
    async addScrap(newsId) {
      await latency(120);
      const u = requireUser();
      if (!NEWS_BY_ID.has(newsId)) fail(404, "NEWS_NOT_FOUND", "뉴스를 찾을 수 없습니다.");
      const s = userState(u.userId);
      if (s.scraps[newsId]) fail(409, "NEWS_SCRAP_DUPLICATED", "이미 스크랩한 뉴스입니다.");
      const at = new Date().toISOString();
      s.scraps[newsId] = at;
      saveUserState(u.userId, s);
      return { newsId, scrappedAt: at };
    },
    async removeScrap(newsId) {
      await latency(100);
      const u = requireUser();
      const s = userState(u.userId);
      delete s.scraps[newsId];
      saveUserState(u.userId, s);
    },
    async watchCompanies(p) {
      await latency();
      const u = requireUser();
      const s = userState(u.userId);
      const items = Object.entries(s.watch)
        .map(([companyId, watchedAt]) => ({ c: COMPANY_BY_ID.get(companyId), watchedAt }))
        .filter((x): x is { c: MockCompany; watchedAt: string } => Boolean(x.c))
        .sort((a, b) => b.watchedAt.localeCompare(a.watchedAt))
        .map(({ c, watchedAt }) => ({ companyId: c.companyId, name: c.name, stockCode: c.stockCode, market: c.market, primaryIndustry: { industryId: c.industryId, name: c.industry }, watchedAt }));
      return paginate(items, p);
    },
    async addWatch(companyId) {
      await latency(120);
      const u = requireUser();
      if (!COMPANY_BY_ID.has(companyId)) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const s = userState(u.userId);
      if (s.watch[companyId]) fail(409, "WATCH_COMPANY_DUPLICATED", "이미 등록한 관심 기업입니다.");
      const at = new Date().toISOString();
      s.watch[companyId] = at;
      saveUserState(u.userId, s);
      return { companyId, watchedAt: at };
    },
    async removeWatch(companyId) {
      await latency(100);
      const u = requireUser();
      const s = userState(u.userId);
      delete s.watch[companyId];
      saveUserState(u.userId, s);
    },
  },

  companies: {
    async list(p) {
      await latency();
      const u = currentUser();
      const watch = u ? userState(u.userId).watch : {};
      const kw = p?.keyword?.trim().toLowerCase();
      let items = COMPANIES.slice();
      if (p?.market) items = items.filter((c) => c.market === p.market);
      if (p?.industryId) items = items.filter((c) => c.industryId === p.industryId);
      if (kw) {
        const scored = items
          .map((c) => {
            const hay = [c.name, c.nameEn, c.stockCode].filter((v): v is string => Boolean(v)).map((v) => v.toLowerCase());
            const alias = c.aliases.map((v) => v.toLowerCase());
            let score = 0;
            if (hay.some((h) => h === kw)) score = 4;
            else if (hay.some((h) => h.startsWith(kw))) score = 3;
            else if (hay.some((h) => h.includes(kw))) score = 2;
            // 별칭(company_alias) 매치는 정식 명칭보다 한 단계 낮은 가중치로 둔다
            else if (alias.some((h) => h === kw || h.startsWith(kw) || h.includes(kw))) score = 1;
            return { c, score };
          })
          .filter((x) => x.score > 0)
          .sort((a, b) => b.score - a.score || a.c.name.localeCompare(b.c.name, "ko"));
        items = scored.map((x) => x.c);
      } else {
        items.sort((a, b) => a.name.localeCompare(b.name, "ko"));
      }
      return paginate(items.map((c) => toSummary(c, watch)), p);
    },
    async detail(companyId) {
      await latency(110);
      const c = COMPANY_BY_ID.get(companyId);
      if (!c) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const u = currentUser();
      const watch = u ? userState(u.userId).watch : {};
      return {
        companyId: c.companyId,
        name: c.name,
        nameEn: c.nameEn,
        stockCode: c.stockCode,
        market: c.market,
        description: c.description,
        industries: [{ industryId: c.industryId, name: c.industry, primary: true }],
        watched: Boolean(watch[c.companyId]),
        listedShares: c.listedShares,
        marketCap: marketCapOf(c),
      };
    },
    async metrics(companyId, window = "30D") {
      await latency(120);
      if (!COMPANY_BY_ID.has(companyId)) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const m = metricsFor(companyId, window);
      return { companyId, window, ...m, measuredAt: currentSnapshot().asOfAt };
    },
    async metricsHistory(companyId, params) {
      await latency(160);
      if (!COMPANY_BY_ID.has(companyId)) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const window = params?.window ?? "30D";
      const today = new Date();
      today.setUTCHours(0, 0, 0, 0);
      // from·to 가 오면 그 구간을, 없으면 window 일수만큼 오늘까지 생성한다
      const to = params?.to ? Date.parse(`${params.to}T00:00:00Z`) : today.getTime();
      const from = params?.from ? Date.parse(`${params.from}T00:00:00Z`) : to - (windowDays(window) - 1) * DAY;
      if (Number.isNaN(from) || Number.isNaN(to)) fail(400, "VALIDATION_FAILED", "from·to 는 YYYY-MM-DD 형식이어야 합니다.");
      const items: T.MetricPoint[] = [];
      // 지나치게 긴 구간을 요청해도 목업이 멈추지 않도록 1년으로 자른다
      const start = Math.max(from, to - 365 * DAY);
      for (let at = start; at <= to; at += DAY) {
        const m = metricsFor(companyId, "7D", at + DAY - 1);
        items.push({ measuredAt: new Date(at + DAY - 1).toISOString(), ...m });
      }
      return { companyId, window, items };
    },
    async stockPrices(companyId, period = "1M") {
      await latency(140);
      const c = COMPANY_BY_ID.get(companyId);
      if (!c) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const all = stockSeries(c);
      const n = period === "1M" ? 22 : period === "3M" ? 65 : period === "6M" ? 130 : all.length;
      const items = all.slice(-n);
      return { companyId, period, interval: "1D", asOfAt: items.at(-1)?.tradingAt ?? new Date().toISOString(), items };
    },
    async industries() {
      await latency(90);
      return { items: INDUSTRIES.map((i) => ({ ...i })) };
    },
  },

  news: {
    async list(p) {
      await latency();
      const u = currentUser();
      const scraps = u ? userState(u.userId).scraps : {};
      let items = p?.companyId ? (NEWS_BY_COMPANY.get(p.companyId) ?? []) : NEWS;
      if (p?.industryId) items = items.filter((n) => n.related.some((r) => COMPANY_BY_ID.get(r.companyId)?.industryId === p.industryId));
      if (p?.sentiment) items = items.filter((n) => (p.companyId ? n.related.find((r) => r.companyId === p.companyId)?.sentiment : n.sentiment) === p.sentiment);
      if (p?.keyword) {
        const kw = p.keyword.toLowerCase();
        items = items.filter((n) => n.title.toLowerCase().includes(kw));
      }
      if (p?.from) {
        const f = Date.parse(p.from);
        items = items.filter((n) => n.publishedMs >= f);
      }
      if (p?.to) {
        const t = Date.parse(p.to) + DAY;
        items = items.filter((n) => n.publishedMs < t);
      }
      return paginate(items.map((n) => toNewsItem(n, scraps)), p);
    },
    async detail(newsId) {
      await latency(120);
      const n = NEWS_BY_ID.get(newsId);
      if (!n) fail(404, "NEWS_NOT_FOUND", "뉴스를 찾을 수 없습니다.");
      const u = currentUser();
      const scraps = u ? userState(u.userId).scraps : {};
      return {
        newsId: n.newsId,
        title: n.title,
        summary: n.summary,
        publisher: n.publisher,
        author: n.author,
        originalUrl: n.originalUrl,
        publishedAt: n.publishedAt,
        relatedCompanies: n.related.map((r) => ({ companyId: r.companyId, name: COMPANY_BY_ID.get(r.companyId)?.name ?? "", sentiment: r.sentiment, relevanceScore: r.relevanceScore, impactScore: r.impactScore })),
        evidence: n.evidence,
        scrapped: Boolean(scraps[n.newsId]),
      };
    },
  },

  graphs: {
    async latest(p) {
      await latency(260);
      // 목업이 그래프를 만드는 방식일 뿐 요청 파라미터가 아니다 — 명세의 전체 그래프는 노드·간선 수 상한이 없다
      const perCompany = 5;
      if (p?.industryId && !INDUSTRIES.some((i) => i.industryId === p.industryId)) fail(400, "VALIDATION_FAILED", "지원하지 않는 산업입니다.");
      const nodes = p?.industryId ? COMPANIES.filter((c) => c.industryId === p.industryId) : COMPANIES;
      const nodeIds = new Set(nodes.map((c) => c.companyId));
      // 기업별 상위 N 간선 — 어느 한쪽 끝점의 상위 N 에 들면 유지
      const keep = new Set<string>();
      nodes.forEach((c) => {
        (ADJACENCY.get(c.companyId) ?? [])
          .filter((e) => nodeIds.has(otherEnd(e, c.companyId)))
          .slice(0, perCompany)
          .forEach((e) => keep.add(e.relationshipId));
      });
      const edges = EDGES.filter((e) => keep.has(e.relationshipId)).map((e) => toEdge(e));
      const snap = currentSnapshot();
      return {
        ...snap,
        personalized: false,
        // priceChange 는 은하 뷰의 행성 높이(주가 고도)가 읽는다 — 목업은 같은 시계열에서 뽑아 화면과 주가 탭이 어긋나지 않는다
        nodes: nodes.map((c) => ({ companyId: c.companyId, name: c.name, stockCode: c.stockCode, market: c.market, industryName: c.industry, priceChange: priceChanges(c), marketCapKrw: marketCapKrwOf(c) })),
        edges,
      };
    },
    async company(companyId, p) {
      await latency(240);
      const center = COMPANY_BY_ID.get(companyId);
      if (!center) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const maxDepth = Math.min(3, Math.max(1, p?.maxDepth ?? 3));
      // 명세가 받는 파라미터는 maxDepth 뿐이다 — 아래 둘은 목업이 표본을 추리는 내부 기준
      const perNode = 3;
      const maxNodes = 100;

      const depth = new Map<string, number>([[companyId, 0]]);
      const order: string[] = [companyId];
      let frontier = [companyId];
      for (let d = 1; d <= maxDepth && order.length < maxNodes; d += 1) {
        const next: string[] = [];
        for (const id of frontier) {
          const adj = (ADJACENCY.get(id) ?? []).filter((e) => !depth.has(otherEnd(e, id))).slice(0, perNode);
          for (const e of adj) {
            const o = otherEnd(e, id);
            if (depth.has(o) || order.length >= maxNodes) continue;
            depth.set(o, d);
            order.push(o);
            next.push(o);
          }
        }
        frontier = next;
      }
      const included = new Set(order);
      const kept = EDGES.filter((e) => included.has(e.sourceCompanyId) && included.has(e.targetCompanyId));
      // depth 는 "돌려주는 간선 기준 최단 홉" 이어야 한다(계약: 1 = 직접 관계). 위 확장은 노드당 상위 perNode 개만 따라가므로
      // 나중 단계에서 들어온 기업이 중심과 직접 간선을 갖는 경우가 생기는데, 그대로 두면 은하 뷰에서 중심이 depth 3 행성까지 긴 빔을 뻗는다
      const adjacency = new Map<string, string[]>();
      kept.forEach((e) => {
        for (const [a, b] of [[e.sourceCompanyId, e.targetCompanyId], [e.targetCompanyId, e.sourceCompanyId]]) {
          if (!adjacency.has(a)) adjacency.set(a, []);
          adjacency.get(a)!.push(b);
        }
      });
      const hop = new Map<string, number>([[companyId, 0]]);
      for (let queue = [companyId]; queue.length; ) {
        const next: string[] = [];
        for (const id of queue) for (const o of adjacency.get(id) ?? []) if (!hop.has(o)) {
          hop.set(o, hop.get(id)! + 1);
          next.push(o);
        }
        queue = next;
      }
      const edges = kept.map((e) => toEdge(e));
      const snap = currentSnapshot();
      return {
        snapshotId: snap.snapshotId,
        asOfAt: snap.asOfAt,
        centerCompanyId: companyId,
        personalized: false,
        nodes: order.map((id) => ({ companyId: id, name: COMPANY_BY_ID.get(id)!.name, depth: Math.min(depth.get(id)!, hop.get(id) ?? depth.get(id)!) })),
        edges,
      };
    },
    async relationship(relationshipId, window = "30D") {
      await latency(130);
      const e = EDGE_BY_ID.get(relationshipId);
      if (!e) fail(404, "RELATIONSHIP_NOT_FOUND", "기업 관계를 찾을 수 없습니다.");
      // 윈도우별 점수: 해당 기간 근거 뉴스 비중으로 살짝 변동
      const days = windowDays(window);
      const from = Date.now() - days * DAY;
      const evidences = NEWS.filter((n) => n.relationshipId === relationshipId);
      const inWindow = evidences.filter((n) => n.publishedMs >= from).length;
      const ratio = evidences.length ? inWindow / evidences.length : 1;
      const adj = window === "90D" ? 1 : window === "30D" ? 0.85 + ratio * 0.25 : 0.7 + ratio * 0.5;
      // 공통 점수와 구성 점수에 같은 보정을 건다 — 프론트가 어떤 비율로 섞어도 기간 감각이 어긋나지 않는다
      const adjust = (v: number | null) => (v === null ? null : Math.round(Math.min(100, v * adj) * 10) / 10);
      const score = adjust(e.score)!;
      const s = COMPANY_BY_ID.get(e.sourceCompanyId)!;
      const t = COMPANY_BY_ID.get(e.targetCompanyId)!;
      return {
        relationshipId,
        sourceCompany: { companyId: s.companyId, name: s.name },
        targetCompany: { companyId: t.companyId, name: t.name },
        relationshipType: e.relationshipType,
        directionality: isDirected(e.relationshipType) ? "DIRECTED" : "UNDIRECTED",
        window,
        score,
        newsScore: adjust(e.newsScore),
        disclosureScore: adjust(e.disclosureScore),
        impactDirection: e.impactDirection,
        confidence: e.confidence,
        evidenceCount: Math.max(inWindow, Math.round(e.evidenceCount * (window === "90D" ? 1 : window === "30D" ? 0.6 : 0.25))),
        asOfAt: currentSnapshot().asOfAt,
        personalized: false,
      };
    },
    async evidence(relationshipId, p) {
      await latency(150);
      if (!EDGE_BY_ID.has(relationshipId)) fail(404, "RELATIONSHIP_NOT_FOUND", "기업 관계를 찾을 수 없습니다.");
      const items: T.RelationshipEvidence[] = NEWS.filter((n) => n.relationshipId === relationshipId).map((n) => ({
        newsId: n.newsId,
        title: n.title,
        summary: n.summary,
        publisher: n.publisher,
        originalUrl: n.originalUrl,
        publishedAt: n.publishedAt,
        evidenceSentence: n.evidence[0]?.sentence ?? n.summary,
        contributionScore: n.contributionScore,
        confidence: n.evidence[0]?.confidence ?? 0.8,
      }));
      return paginate(items, { cursor: p?.cursor, size: p?.size ?? 10 }, 20);
    },
  },

  community: {
    async latest(p) {
      await latency();
      const items: T.GlobalComment[] = allComments().map((c) => ({
        commentId: c.commentId,
        content: c.content,
        author: { userId: c.userId, nickname: c.nickname },
        company: { companyId: c.companyId, name: COMPANY_BY_ID.get(c.companyId)?.name ?? "" },
        createdAt: c.createdAt,
        updatedAt: c.updatedAt,
        edited: c.edited,
      }));
      return paginate(items, p);
    },
    async byCompany(companyId, p) {
      await latency();
      if (!COMPANY_BY_ID.has(companyId)) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const items: T.CompanyComment[] = allComments()
        .filter((c) => c.companyId === companyId)
        .map((c) => ({ commentId: c.commentId, content: c.content, author: { userId: c.userId, nickname: c.nickname }, companyId: c.companyId, createdAt: c.createdAt, updatedAt: c.updatedAt, edited: c.edited }));
      return paginate(items, p);
    },
    async write(companyId, content) {
      await latency(180);
      const u = requireUser();
      if (!COMPANY_BY_ID.has(companyId)) fail(404, "COMPANY_NOT_FOUND", "기업을 찾을 수 없습니다.");
      const text = content.trim();
      if (!text || text.length > 500) fail(400, "VALIDATION_FAILED", "댓글은 1~500자여야 합니다.", [{ field: "content", message: "길이 오류" }]);
      const now = new Date().toISOString();
      const c: StoredComment = { commentId: crypto.randomUUID(), companyId, userId: u.userId, nickname: u.nickname, content: text, createdAt: now, updatedAt: now, edited: false };
      upsertComment(c);
      return { commentId: c.commentId, companyId, content: text, createdAt: now, updatedAt: now };
    },
    async edit(commentId, content) {
      await latency(160);
      const u = requireUser();
      const c = allComments().find((x) => x.commentId === commentId);
      if (!c) fail(404, "COMMENT_NOT_FOUND", "댓글을 찾을 수 없습니다.");
      if (c.userId !== u.userId) fail(403, "COMMENT_FORBIDDEN", "본인 댓글만 수정할 수 있습니다.");
      const text = content.trim();
      if (!text || text.length > 500) fail(400, "VALIDATION_FAILED", "댓글은 1~500자여야 합니다.", [{ field: "content", message: "길이 오류" }]);
      const next: StoredComment = { ...c, content: text, updatedAt: new Date().toISOString(), edited: true };
      upsertComment(next);
      return { commentId, content: text, createdAt: next.createdAt, updatedAt: next.updatedAt, edited: true };
    },
    async remove(commentId) {
      await latency(120);
      const u = requireUser();
      const c = allComments().find((x) => x.commentId === commentId);
      if (!c) fail(404, "COMMENT_NOT_FOUND", "댓글을 찾을 수 없습니다.");
      if (c.userId !== u.userId) fail(403, "COMMENT_FORBIDDEN", "본인 댓글만 삭제할 수 있습니다.");
      upsertComment({ ...c, deleted: true });
    },
  },
};
