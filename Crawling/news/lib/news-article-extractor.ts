import { Readability } from "@mozilla/readability";
import { parseHTML } from "linkedom";
import { validatePublicHttpUrl } from "./public-http-url.ts";
import { assertRobotsAllowed, type RobotsAccessRecord } from "./robots-policy.ts";

const DEFAULT_TIMEOUT_MS = 10_000;
const DEFAULT_MAX_RESPONSE_BYTES = 2_000_000;
const DEFAULT_MIN_CONTENT_CHARS = 200;
const DEFAULT_MAX_CONTENT_CHARS = 80_000;
const DEFAULT_MAX_REDIRECTS = 4;

export type ArticleExtractionMethod = "json-ld" | "readability" | "naver-selector" | "article-selector";

export type ExtractedNewsArticle = {
  requestedUrl: string;
  finalUrl: string;
  title: string;
  byline?: string;
  siteName?: string;
  publishedTime?: string;
  excerpt?: string;
  textContent: string;
  charCount: number;
  wordCount: number;
  method: ArticleExtractionMethod;
  truncated: boolean;
  robots: RobotsAccessRecord;
};

export type ArticleFetchOptions = {
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  maxResponseBytes?: number;
  maxContentChars?: number;
  minContentChars?: number;
  maxRedirects?: number;
  userAgent?: string;
};

type ArticleCandidate = {
  title?: string;
  byline?: string;
  siteName?: string;
  publishedTime?: string;
  excerpt?: string;
  textContent: string;
  method: ArticleExtractionMethod;
};

type JsonRecord = Record<string, unknown>;

export async function extractNewsArticle(
  inputUrl: string,
  options: ArticleFetchOptions = {},
): Promise<ExtractedNewsArticle> {
  const requestedUrl = validatePublicArticleUrl(inputUrl).toString();
  const timeoutMs = positiveInteger(options.timeoutMs, DEFAULT_TIMEOUT_MS);
  const maxResponseBytes = positiveInteger(options.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES);
  const maxRedirects = positiveInteger(options.maxRedirects, DEFAULT_MAX_REDIRECTS);
  const fetchImpl = options.fetchImpl ?? fetch;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error("article_fetch_timeout")), timeoutMs);

  try {
    let currentUrl = requestedUrl;
    const userAgent = options.userAgent ?? "NexusRiskArticleExtractor/0.1 (+local development test)";
    for (let redirectCount = 0; redirectCount <= maxRedirects; redirectCount += 1) {
      const robots = await assertRobotsAllowed(currentUrl, {
        fetchImpl,
        signal: controller.signal,
        userAgent,
      });
      const response = await fetchImpl(currentUrl, {
        method: "GET",
        redirect: "manual",
        signal: controller.signal,
        headers: {
          accept: "text/html,application/xhtml+xml;q=0.9",
          "accept-language": "ko-KR,ko;q=0.9,en;q=0.6",
          "user-agent": userAgent,
        },
      });

      if (isRedirect(response.status)) {
        const location = response.headers.get("location");
        await response.body?.cancel();
        if (!location) throw new Error(`article_redirect_without_location:${response.status}`);
        if (redirectCount === maxRedirects) throw new Error("article_redirect_limit_exceeded");
        currentUrl = validatePublicArticleUrl(new URL(location, currentUrl).toString()).toString();
        continue;
      }

      if (!response.ok) {
        await response.body?.cancel();
        throw new Error(`article_fetch_http_${response.status}`);
      }

      const contentType = response.headers.get("content-type") ?? "";
      if (contentType && !/\b(?:text\/html|application\/xhtml\+xml)\b/i.test(contentType)) {
        await response.body?.cancel();
        throw new Error(`article_unsupported_content_type:${contentType.split(";")[0]}`);
      }

      const bytes = await readLimitedBody(response, maxResponseBytes);
      const html = decodeHtml(bytes, contentType);
      const finalUrl = validatePublicArticleUrl(response.url || currentUrl).toString();
      const extracted = extractNewsArticleFromHtml(html, finalUrl, options);
      return { requestedUrl, finalUrl, ...extracted, robots };
    }
    throw new Error("article_redirect_limit_exceeded");
  } catch (error) {
    if (controller.signal.aborted) throw new Error("article_fetch_timeout", { cause: error });
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function extractNewsArticleFromHtml(
  html: string,
  finalUrl: string,
  options: Pick<ArticleFetchOptions, "minContentChars" | "maxContentChars"> = {},
): Omit<ExtractedNewsArticle, "requestedUrl" | "finalUrl" | "robots"> {
  const minContentChars = positiveInteger(options.minContentChars, DEFAULT_MIN_CONTENT_CHARS);
  const maxContentChars = positiveInteger(options.maxContentChars, DEFAULT_MAX_CONTENT_CHARS);
  const { document } = parseHTML(html);
  if (/기사번호가\s*누락되어\s*있거나\s*잘못된\s*접근입니다/.test(html)) {
    throw new Error("article_not_found");
  }
  const jsonLdCandidate = extractJsonLdCandidate(document);
  const readabilityCandidate = extractReadabilityCandidate(document);
  const naverSelectorCandidate = extractSelectorCandidate(document, [
    "#dic_area",
    "#newsct_article",
    "#newsEndContents",
    "#articeBody",
    ".go_trans._article_content",
  ], "naver-selector");
  const selectorCandidate = extractSelectorCandidate(document, [
    '[itemprop="articleBody"]',
    "article",
    "#articleBody",
    "#article-body",
    ".article-body",
    ".article_view",
    ".article-content",
    ".news_body",
    ".news_body_area",
    ".newsct_article",
    ".view_cont",
  ], "article-selector");
  const candidates = [jsonLdCandidate, readabilityCandidate, naverSelectorCandidate, selectorCandidate]
    .filter((candidate): candidate is ArticleCandidate => Boolean(candidate))
    .filter((candidate) => candidate.textContent.length >= minContentChars);
  const best = candidates.sort(compareCandidates)[0];
  if (!best) throw new Error("article_body_not_found");

  const truncated = best.textContent.length > maxContentChars;
  const textContent = truncated
    ? `${best.textContent.slice(0, maxContentChars).trimEnd()}…`
    : best.textContent;
  const title = cleanInlineText(best.title) || readMetaContent(document, "property", "og:title")
    || cleanInlineText(document.title) || "제목 없음";
  const excerpt = cleanInlineText(best.excerpt) || readMetaContent(document, "name", "description") || undefined;

  return {
    title,
    byline: cleanInlineText(best.byline) || undefined,
    siteName: cleanInlineText(best.siteName) || readMetaContent(document, "property", "og:site_name") || undefined,
    publishedTime: cleanInlineText(best.publishedTime)
      || readMetaContent(document, "property", "article:published_time") || undefined,
    excerpt,
    textContent,
    charCount: textContent.length,
    wordCount: countWords(textContent),
    method: best.method,
    truncated,
  };
}

export function validatePublicArticleUrl(input: string): URL {
  return validatePublicHttpUrl(input, "article");
}

function extractJsonLdCandidate(document: Document): ArticleCandidate | null {
  const records: JsonRecord[] = [];
  for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]'))) {
    const value = script.textContent?.trim();
    if (!value) continue;
    try {
      collectJsonRecords(JSON.parse(value) as unknown, records);
    } catch {
      // 일부 언론사는 JSON-LD와 유사하지만 유효하지 않은 스크립트를 포함한다.
    }
  }

  const candidates = records.flatMap((record): ArticleCandidate[] => {
    const articleBody = typeof record.articleBody === "string" ? normalizeArticleText(record.articleBody) : "";
    if (!articleBody) return [];
    return [{
      title: stringValue(record.headline) || stringValue(record.name),
      byline: authorName(record.author),
      siteName: publisherName(record.publisher),
      publishedTime: stringValue(record.datePublished),
      excerpt: stringValue(record.description),
      textContent: articleBody,
      method: "json-ld",
    }];
  });
  return candidates.sort((left, right) => right.textContent.length - left.textContent.length)[0] ?? null;
}

function extractReadabilityCandidate(document: Document): ArticleCandidate | null {
  const cloned = document.cloneNode(true) as unknown as Document;
  const article = new Readability(cloned, { charThreshold: 120 }).parse();
  if (!article?.textContent) return null;
  const textFromContent = article.content ? paragraphTextFromHtml(article.content) : "";
  const rawText = normalizeArticleText(article.textContent);
  const textContent = textFromContent.length >= rawText.length * 0.55 ? textFromContent : rawText;
  if (!textContent) return null;
  return {
    title: article.title ?? undefined,
    byline: article.byline ?? undefined,
    siteName: article.siteName ?? undefined,
    publishedTime: article.publishedTime ?? undefined,
    excerpt: article.excerpt ?? undefined,
    textContent,
    method: "readability",
  };
}

function extractSelectorCandidate(
  document: Document,
  selectors: string[],
  method: Extract<ArticleExtractionMethod, "naver-selector" | "article-selector">,
): ArticleCandidate | null {
  const candidates = selectors.flatMap((selector): ArticleCandidate[] => Array.from(document.querySelectorAll(selector)).flatMap((element) => {
    const cloned = element.cloneNode(true) as Element;
    cloned.querySelectorAll("script,style,noscript,nav,aside,footer,form,button,figure,figcaption,.end_photo_org,.img_desc,.reporter_area,.copyright,.promotion").forEach((node) => node.remove());
    cloned.querySelectorAll("br").forEach((node) => node.replaceWith("\n"));
    const textContent = paragraphTextFromElement(cloned);
    return textContent ? [{ textContent, method }] : [];
  }));
  return candidates.sort((left, right) => right.textContent.length - left.textContent.length)[0] ?? null;
}

function compareCandidates(left: ArticleCandidate, right: ArticleCandidate): number {
  const methodScore: Record<ArticleExtractionMethod, number> = { "naver-selector": 4, "json-ld": 3, readability: 2, "article-selector": 1 };
  const leftScore = methodScore[left.method] * 10_000 + Math.min(left.textContent.length, 9_999);
  const rightScore = methodScore[right.method] * 10_000 + Math.min(right.textContent.length, 9_999);
  return rightScore - leftScore;
}

function paragraphTextFromHtml(html: string): string {
  const { document } = parseHTML(`<main>${html}</main>`);
  const main = document.querySelector("main");
  return main ? paragraphTextFromElement(main) : "";
}

function paragraphTextFromElement(element: Element): string {
  const blocks = Array.from(element.querySelectorAll("p,h2,h3,blockquote"))
    .map((node) => cleanInlineText(node.textContent))
    .filter((text) => text.length >= 2)
    .filter((text, index, all) => index === 0 || text !== all[index - 1]);
  return normalizeArticleText(blocks.length ? blocks.join("\n\n") : element.textContent ?? "");
}

function collectJsonRecords(value: unknown, output: JsonRecord[]): void {
  if (Array.isArray(value)) {
    value.forEach((item) => collectJsonRecords(item, output));
    return;
  }
  if (!value || typeof value !== "object") return;
  const record = value as JsonRecord;
  output.push(record);
  Object.values(record).forEach((item) => collectJsonRecords(item, output));
}

function authorName(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(authorName).filter(Boolean).join(", ") || undefined;
  return value && typeof value === "object" ? stringValue((value as JsonRecord).name) : undefined;
}

function publisherName(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  return value && typeof value === "object" ? stringValue((value as JsonRecord).name) : undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readMetaContent(document: Document, attribute: "name" | "property", value: string): string {
  return cleanInlineText(document.querySelector(`meta[${attribute}="${value}"]`)?.getAttribute("content"));
}

function normalizeArticleText(value: string): string {
  return value
    .replace(/\u00a0/g, " ")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[\t ]+/g, " ").trim())
    .filter((line, index, all) => Boolean(line) && (index === 0 || line !== all[index - 1]))
    .join("\n\n")
    .trim();
}

function cleanInlineText(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function countWords(value: string): number {
  return value.split(/\s+/u).filter(Boolean).length;
}

function positiveInteger(value: number | undefined, fallback: number): number {
  return Number.isInteger(value) && (value ?? 0) > 0 ? value! : fallback;
}

function isRedirect(status: number): boolean {
  return status === 301 || status === 302 || status === 303 || status === 307 || status === 308;
}

async function readLimitedBody(response: Response, maxBytes: number): Promise<Uint8Array> {
  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > maxBytes) {
    await response.body?.cancel();
    throw new Error("article_response_too_large");
  }
  if (!response.body) return new Uint8Array();

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new Error("article_response_too_large");
    }
    chunks.push(value);
  }

  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return output;
}

function decodeHtml(bytes: Uint8Array, contentType: string): string {
  const head = new TextDecoder("utf-8", { fatal: false }).decode(bytes.slice(0, 8192));
  const declared = contentType.match(/charset\s*=\s*["']?([^;\s"']+)/i)?.[1]
    ?? head.match(/<meta[^>]+charset\s*=\s*["']?([^\s"'/>]+)/i)?.[1]
    ?? head.match(/<meta[^>]+content=["'][^"']*charset=([^\s"';]+)/i)?.[1]
    ?? "utf-8";
  const charset = /^(?:ks_c_5601-1987|x-windows-949|cp949)$/i.test(declared) ? "euc-kr" : declared;
  try {
    return new TextDecoder(charset, { fatal: false }).decode(bytes);
  } catch {
    return new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  }
}
