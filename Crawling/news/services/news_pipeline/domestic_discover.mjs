import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { extractNewsArticle } from "../../lib/news-article-extractor.ts";
import { assertRobotsAllowed } from "../../lib/robots-policy.ts";
import { validatePublicHttpUrl } from "../../lib/public-http-url.ts";

const USER_AGENT = "NexusRiskArticleExtractor/1.0 (+news intelligence; source-linked)";
const MAX_DOCUMENT_BYTES = 5_000_000;
const MAX_CURSOR_BYTES = 60_000;
const TRACKING_PARAMETERS = new Set(["ref", "inflow", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]);

// The fixed sources and URL rules are retained from the existing MySQL crawler.
export const SOURCES = {
  kpenews: { organization: "한국정경신문", domain: "kpenews.com", delayMs: 5_000, roots: ["https://kpenews.com/sitemap_googlenews.xml"] },
  mdtoday: { organization: "메디컬투데이", domain: "mdtoday.co.kr", delayMs: 1_500, roots: ["https://www.mdtoday.co.kr/sitemap.xml"] },
  sedaily: { organization: "서울경제", domain: "sedaily.com", delayMs: 1_500, roots: ["https://www.sedaily.com/sitemap/latestnews"] },
  newspim: { organization: "뉴스핌", domain: "newspim.com", delayMs: 1_200, roots: ["https://www.newspim.com/sitemap/recent/all_1", "https://www.newspim.com/sitemap/recent/all_2"] },
  newstomato: { organization: "뉴스토마토", domain: "newstomato.com", delayMs: 1_500, roots: ["http://www.newstomato.com/"], html: true },
  hellot: { organization: "헬로티", domain: "hellot.net", delayMs: 1_000, roots: [], urlTemplate: "https://www.hellot.net/news/article.html?no={id}" },
};

function sourceConfig(source) {
  if (!Object.hasOwn(SOURCES, source)) throw new Error("unknown_source");
  return SOURCES[source];
}

export function normalizeArticleUrl(value) {
  const url = validatePublicHttpUrl(value);
  url.hash = "";
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMETERS.has(key.toLowerCase())) url.searchParams.delete(key);
  }
  return url.toString().replace(/\/$/, "");
}

function sourceUrl(value, source) {
  const url = validatePublicHttpUrl(value);
  if (url.hostname.toLowerCase().replace(/^www\./, "") !== sourceConfig(source).domain || url.port) {
    throw new Error("source_url_mismatch");
  }
  return url;
}

export function articleId(value, source) {
  let url;
  try { url = sourceUrl(value, source); } catch { return null; }
  let id;
  if (source === "kpenews" && url.pathname.toLowerCase() === "/view.aspx") id = url.searchParams.get("No") ?? url.searchParams.get("no");
  if (source === "mdtoday" && url.pathname === "/news/articleView.html") id = url.searchParams.get("idxno");
  if (source === "sedaily") id = url.pathname.match(/^\/article\/(\d+)\/?$/i)?.[1];
  if (source === "newspim") id = url.pathname.match(/^\/news\/view\/(\d+)\/?$/i)?.[1];
  if (source === "newstomato" && url.pathname.toLowerCase() === "/readnews.aspx") id = url.searchParams.get("no");
  if (source === "hellot" && url.pathname === "/news/article.html") id = url.searchParams.get("no");
  return id && /^\d{1,24}$/.test(id) && BigInt(id) > 0n ? BigInt(id).toString() : null;
}

export function decodeXml(value) {
  return value.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&#(x[\da-f]+|\d+);/gi, (_, code) => {
      const point = code[0].toLowerCase() === "x" ? parseInt(code.slice(1), 16) : Number(code);
      return point > 0 && point <= 0x10ffff ? String.fromCodePoint(point) : "";
    }).replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}

function elementText(xml, tag) {
  return decodeXml(xml.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, "i"))?.[1] ?? "").trim();
}

export function parseDiscoveryDocument(text, documentUrl, source) {
  const candidates = new Map();
  const documents = new Set();
  const add = (value, title = "", publishedAt = "") => {
    try {
      const url = normalizeArticleUrl(new URL(value, documentUrl).toString());
      const externalId = articleId(url, source);
      if (externalId) candidates.set(url, { url, title, source, organization: sourceConfig(source).organization, external_id: externalId, published_at: validDate(publishedAt) });
    } catch { /* Invalid and foreign links are not crawl targets. */ }
  };
  for (const match of text.matchAll(/<sitemap(?:\s[^>]*)?>([\s\S]*?)<\/sitemap>/gi)) {
    try {
      const url = sourceUrl(new URL(elementText(match[1], "loc"), documentUrl).toString(), source).toString();
      documents.add(url);
    } catch { /* A sitemap cannot expand crawling to another publisher. */ }
  }
  const urlBlocks = [...text.matchAll(/<url(?:\s[^>]*)?>([\s\S]*?)<\/url>/gi)];
  for (const match of urlBlocks) {
    add(elementText(match[1], "loc"), elementText(match[1], "news:title"), elementText(match[1], "news:publication_date") || elementText(match[1], "lastmod"));
  }
  if (!urlBlocks.length && !documents.size) {
    for (const match of text.matchAll(/<loc(?:\s[^>]*)?>\s*([\s\S]*?)\s*<\/loc>/gi)) add(decodeXml(match[1]));
  }
  if (sourceConfig(source).html) {
    for (const match of text.matchAll(/href\s*=\s*["']([^"']+)["']/gi)) add(decodeXml(match[1]));
  }
  return { candidates: [...candidates.values()].sort(compareCandidates), documents: [...documents] };
}

function compareCandidates(left, right) {
  const a = BigInt(left.external_id), b = BigInt(right.external_id);
  return a === b ? left.url.localeCompare(right.url, "en") : a > b ? -1 : 1;
}

function integer(value, fallback, minimum, maximum, name) {
  const number = value === undefined ? fallback : Number(value);
  if (!Number.isSafeInteger(number) || number < minimum || number > maximum) throw new Error(`invalid_${name}`);
  return number;
}

function encodeCursor(state) {
  const encoded = Buffer.from(JSON.stringify(state)).toString("base64url");
  if (Buffer.byteLength(encoded) > MAX_CURSOR_BYTES) throw new Error("discovery_cursor_capacity_exceeded");
  return encoded;
}

function decodeCursor(value, source) {
  if (typeof value !== "string" || value.length > MAX_CURSOR_BYTES || !/^[\w-]+$/.test(value)) throw new Error("invalid_cursor");
  let state;
  try { state = JSON.parse(Buffer.from(value, "base64url").toString("utf8")); } catch { throw new Error("invalid_cursor"); }
  if (state.version !== 1 || state.source !== source) throw new Error("cursor_source_mismatch");
  return state;
}

function makeRequester(source, options) {
  const sleep = options.sleep ?? ((ms) => new Promise((resolveDelay) => setTimeout(resolveDelay, ms)));
  const now = options.now ?? Date.now;
  const fetchImpl = options.fetchImpl ?? fetch;
  const lastRequest = new Map();
  const delayMs = Math.max(1_000, sourceConfig(source).delayMs);
  return async (input, init) => {
    const url = validatePublicHttpUrl(String(input));
    const host = url.hostname.toLowerCase();
    // Also wait before the first request: separate one-shot CLI processes cannot
    // share an in-memory host limiter, and must not burst at process boundaries.
    const elapsed = now() - (lastRequest.get(host) ?? now());
    if (elapsed < delayMs) await sleep(delayMs - elapsed);
    lastRequest.set(host, now());
    return fetchImpl(input, init);
  };
}

async function readBoundedText(response) {
  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > MAX_DOCUMENT_BYTES) {
    await response.body?.cancel();
    throw new Error("discovery_response_too_large");
  }
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks = [];
  let bytes = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_DOCUMENT_BYTES) throw new Error("discovery_response_too_large");
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  return new TextDecoder("utf-8").decode(Buffer.concat(chunks));
}

async function fetchDiscoveryDocument(url, source, request) {
  let current = sourceUrl(url, source).toString();
  for (let redirects = 0; redirects <= 4; redirects += 1) {
    const signal = AbortSignal.timeout(45_000);
    await assertRobotsAllowed(current, { fetchImpl: request, signal, userAgent: USER_AGENT });
    const response = await request(current, {
      headers: { accept: "application/xml,text/xml,text/html;q=0.9,*/*;q=0.1", "accept-language": "ko-KR,ko;q=0.9,en;q=0.5", "user-agent": USER_AGENT },
      redirect: "manual", signal,
    });
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      await response.body?.cancel();
      if (!location) throw new Error("discovery_redirect_without_location");
      current = sourceUrl(new URL(location, current).toString(), source).toString();
      continue;
    }
    if (!response.ok) { await response.body?.cancel(); throw new Error(`discovery_fetch_http_${response.status}`); }
    return readBoundedText(response);
  }
  throw new Error("discovery_too_many_redirects");
}

export async function discover(options) {
  const source = options.source;
  const config = sourceConfig(source);
  const maxUrls = integer(options.maxUrls, 100, 1, 1_000, "max_urls");
  const maxPages = integer(options.maxPages, 3, 1, 10, "max_pages");
  if (source === "hellot") return discoverHellot(options, maxUrls);
  const state = options.cursor ? decodeCursor(options.cursor, source) : { version: 1, source, pending: [...config.roots], visited: [], current: null, after: null };
  if (!Array.isArray(state.pending) || !Array.isArray(state.visited) || state.pending.length + state.visited.length > 500) throw new Error("invalid_cursor_documents");
  [...state.pending, ...state.visited, ...(state.current ? [state.current] : [])].forEach((url) => sourceUrl(url, source));
  if (state.after && (!/^\d{1,24}$/.test(state.after.external_id) || !articleId(state.after.url, source))) throw new Error("invalid_cursor_anchor");
  const request = makeRequester(source, options);
  const candidates = [];
  let pagesFetched = 0;
  while (candidates.length < maxUrls && pagesFetched < maxPages && (state.current || state.pending.length)) {
    const documentUrl = state.current ?? state.pending.shift();
    const text = await fetchDiscoveryDocument(documentUrl, source, request);
    pagesFetched += 1;
    const parsed = parseDiscoveryDocument(text, documentUrl, source);
    for (const child of parsed.documents) {
      if (child !== documentUrl && !state.visited.includes(child) && !state.pending.includes(child)) state.pending.push(child);
    }
    if (state.pending.length + state.visited.length > 500) throw new Error("discovery_document_capacity_exceeded");
    const available = parsed.candidates.filter((candidate) => !state.after || compareCandidates(candidate, state.after) > 0);
    const page = available.slice(0, maxUrls - candidates.length);
    candidates.push(...page);
    if (page.length < available.length) {
      const last = page.at(-1);
      state.current = documentUrl;
      state.after = { external_id: last.external_id, url: last.url };
    } else {
      if (!state.visited.includes(documentUrl)) state.visited.push(documentUrl);
      state.current = null;
      state.after = null;
    }
  }
  const done = !state.current && state.pending.length === 0;
  return { status: "ok", source, region: "domestic", candidates, next_cursor: done ? null : encodeCursor(state), done, pages_fetched: pagesFetched, coverage: "currently_published_sitemaps_and_pages", resume_semantics: "Persist all returned candidates before advancing cursor; start a fresh cycle after done to discover newer IDs." };
}

function discoverHellot(options, maxUrls) {
  const minimum = integer(options.minId, 10_000, 1, 2_000_000_000, "min_id");
  const maximum = integer(options.maxId, 114_676, minimum, 2_000_000_000, "max_id");
  const state = options.cursor ? decodeCursor(options.cursor, "hellot") : { version: 1, source: "hellot", min_id: minimum, max_id: maximum, next_id: minimum };
  if (state.min_id !== minimum || state.max_id !== maximum) throw new Error("cursor_range_mismatch");
  let nextId = integer(state.next_id, minimum, minimum, maximum + 1, "cursor_id");
  const candidates = [];
  while (nextId <= maximum && candidates.length < maxUrls) {
    candidates.push({ url: SOURCES.hellot.urlTemplate.replace("{id}", String(nextId)), title: "", source: "hellot", organization: SOURCES.hellot.organization, external_id: String(nextId) });
    nextId += 1;
  }
  state.next_id = nextId;
  const done = nextId > maximum;
  return { status: "ok", source: "hellot", region: "domestic", candidates, next_cursor: done ? null : encodeCursor(state), done, pages_fetched: 0, coverage: "explicit_historical_id_range", min_id: minimum, max_id: maximum, url_template: SOURCES.hellot.urlTemplate };
}

function validDate(value) {
  if (!value) return null;
  const milliseconds = Date.parse(value);
  return Number.isFinite(milliseconds) ? new Date(milliseconds).toISOString() : null;
}

export async function fetchArticle(options) {
  const source = options.source;
  const config = sourceConfig(source);
  const url = normalizeArticleUrl(options.url);
  const externalId = articleId(url, source);
  if (!externalId) throw new Error("article_source_url_mismatch");
  const article = await extractNewsArticle(url, { fetchImpl: makeRequester(source, options), timeoutMs: 60_000, maxResponseBytes: 2_000_000, maxContentChars: 200_000, minContentChars: 80, maxRedirects: 4, userAgent: USER_AGENT });
  let content = article.textContent.replace(/\r/g, "").replace(/[ \t]+\n/g, "\n").trim();
  if (source === "sedaily") content = content.replace(/^뉴스 듣기\s+글자 크기[\s\S]{0,300}?다음 채널구독\s*/u, "");
  content = content.replace(/\n{3,}/g, "\n\n").trim();
  if (content.length < 80) throw new Error("article_content_too_short");
  return {
    status: "ok", url, final_url: article.finalUrl, title: article.title.replace(/^\[(?:헬로티 HelloT|서울경제)\]\s*/i, "").trim(), content,
    organization: article.siteName || config.organization, published_at: validDate(article.publishedTime), source, external_id: externalId, region: "domestic", language: "ko",
    author: article.byline ?? null, summary: article.excerpt ?? null, extraction_method: article.method, truncated: article.truncated, robots: article.robots,
  };
}

export function parseCliArgs(args) {
  const names = new Map([["mode", "mode"], ["source", "source"], ["url", "url"], ["max-urls", "maxUrls"], ["max-pages", "maxPages"], ["min-id", "minId"], ["max-id", "maxId"], ["cursor", "cursor"]]);
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    if (!flag?.startsWith("--") || !names.has(flag.slice(2)) || args[index + 1] === undefined) throw new Error("invalid_arguments");
    options[names.get(flag.slice(2))] = args[index + 1];
  }
  if (!["discover", "fetch"].includes(options.mode)) throw new Error("invalid_mode");
  sourceConfig(options.source);
  if (options.mode === "fetch" && !options.url) throw new Error("missing_url");
  return options;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const options = parseCliArgs(process.argv.slice(2));
    process.stdout.write(`${JSON.stringify(await (options.mode === "discover" ? discover(options) : fetchArticle(options)))}\n`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "domestic_adapter_failed";
    const safe = /^[a-z][a-z0-9_:-]{0,119}$/i.test(message) ? message : "domestic_adapter_failed";
    process.stdout.write(`${JSON.stringify({ status: "error", error: safe })}\n`);
    process.exitCode = 1;
  }
}
