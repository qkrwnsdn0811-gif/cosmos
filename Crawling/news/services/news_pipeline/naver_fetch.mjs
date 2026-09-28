/** Fetch a full article discovered through Naver; API snippets are not bodies. */
import { pathToFileURL } from "node:url";
import { realpathSync } from "node:fs";
import { resolve } from "node:path";
import { extractNewsArticle } from "../../lib/news-article-extractor.ts";

export function assertArticleBody(article) {
  const title = (article.title || "").trim();
  const content = (article.textContent || "").replace(/\s+/g, " ").trim();
  const blockedTitle = /^(?:access (?:denied|required)|just a moment|attention required|forbidden|verify you are human|로그인|접근 (?:제한|거부)|서비스 이용 제한)(?:\b|\s|[!.…|:-])/i;
  const shortBlock = content.length < 2000 && /please (?:enable|turn on).{0,40}(?:cookies|javascript)|checking (?:your )?browser|verify (?:that )?you are (?:a )?human|(?:전체|나머지)\s*(?:기사|내용).{0,60}(?:구독|로그인)/i.test(content);
  if (blockedTitle.test(title + " ") || shortBlock) throw new Error("article_interstitial_or_paywall");
}

export async function fetchNaverArticle(url, options = {}) {
  const article = await extractNewsArticle(url, {
    timeoutMs: 45_000, maxResponseBytes: 2_000_000, maxContentChars: 100_000,
    minContentChars: 80, maxRedirects: 4,
    userAgent: "CosmosNewsCollector/1.0", ...options,
  });
  assertArticleBody(article);
  const content = article.textContent.replace(/\r/g, "").trim();
  if (content.length < 80) throw new Error("article_content_too_short");
  return {
    url: article.requestedUrl, final_url: article.finalUrl, title: article.title,
    content, organization: article.siteName || "", published_at: article.publishedTime || null,
    extraction_method: article.method, truncated: article.truncated, robots: article.robots,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(resolve(process.argv[1]))).href) {
  try {
    if (process.argv.length !== 4 || process.argv[2] !== "--url") throw new Error("invalid_arguments");
    process.stdout.write(JSON.stringify(await fetchNaverArticle(process.argv[3])) + "\n");
  } catch (error) {
    const message = error instanceof Error ? error.message : "article_fetch_failed";
    const safe = /^[a-z][a-z0-9_:-]{0,119}$/i.test(message) ? message : "article_fetch_failed";
    process.stdout.write(JSON.stringify({ status: "error", error: safe }) + "\n");
    process.exitCode = 1;
  }
}
