import assert from "node:assert/strict";
import test from "node:test";
import { articleId, discover, fetchArticle, parseCliArgs, parseDiscoveryDocument } from "../services/news_pipeline/domestic_discover.mjs";
import { clearRobotsPolicyCache } from "../lib/robots-policy.ts";

function fakeNetwork(documents) {
  let now = 0;
  const requested = [];
  const options = {
    now: () => now,
    sleep: async (milliseconds) => { now += milliseconds; },
    fetchImpl: async (url) => {
      requested.push({ url: String(url), at: now });
      if (String(url).endsWith("/robots.txt")) return new Response("User-agent: *\nAllow: /\n");
      const value = documents[String(url)];
      if (value === undefined) throw new Error("unexpected_mock_request");
      return typeof value === "function" ? value() : new Response(value);
    },
  };
  return { options, requested };
}

const sitemap = (ids) => `<urlset>${ids.map((id) => `<url><loc>https://www.newspim.com/news/view/${id}</loc><news:news><news:title><![CDATA[기사 ${id}]]></news:title></news:news></url>`).join("")}</urlset>`;

test("fixed publisher parser retains source metadata and filters foreign/tracking links", () => {
  const xml = `<urlset><url><loc>https://kpenews.com/View.aspx?No=123&amp;utm_source=test</loc><news:title>기업 &amp; 뉴스</news:title><lastmod>2026-09-15T01:00:00Z</lastmod></url><url><loc>https://evil.example/View.aspx?No=999</loc></url></urlset>`;
  const parsed = parseDiscoveryDocument(xml, "https://kpenews.com/sitemap_googlenews.xml", "kpenews");
  assert.equal(parsed.candidates.length, 1);
  assert.equal(parsed.candidates[0].url, "https://kpenews.com/View.aspx?No=123");
  assert.equal(parsed.candidates[0].title, "기업 & 뉴스");
  assert.equal(parsed.candidates[0].organization, "한국정경신문");
  assert.equal(parsed.candidates[0].published_at, "2026-09-15T01:00:00.000Z");
  assert.equal(articleId("https://www.newspim.com/news/view/99999999999999999999", "newspim"), "99999999999999999999");
});

test("HTML discovery and nested sitemap indexes restrict targets to source", () => {
  const page = parseDiscoveryDocument(`<a href="/ReadNews.aspx?no=50">one</a><a href="http://localhost/ReadNews.aspx?no=51">bad</a>`, "http://www.newstomato.com/", "newstomato");
  assert.equal(page.candidates.length, 1);
  const index = parseDiscoveryDocument(`<sitemapindex><sitemap><loc>https://www.mdtoday.co.kr/news.xml</loc></sitemap><sitemap><loc>https://other.example/news.xml</loc></sitemap></sitemapindex>`, "https://www.mdtoday.co.kr/sitemap.xml", "mdtoday");
  assert.deepEqual(index.documents, ["https://www.mdtoday.co.kr/news.xml"]);
});

test("truncated discovery resumes within a page without skipping pending URLs", async () => {
  clearRobotsPolicyCache();
  const root = "https://www.newspim.com/sitemap/recent/all_1";
  const network = fakeNetwork({ [root]: sitemap([104, 103, 102, 101]), "https://www.newspim.com/sitemap/recent/all_2": sitemap([100]) });
  const first = await discover({ source: "newspim", maxUrls: 2, ...network.options });
  assert.deepEqual(first.candidates.map((item) => item.external_id), ["104", "103"]);
  assert.equal(first.done, false);
  const second = await discover({ source: "newspim", maxUrls: 2, cursor: first.next_cursor, ...network.options });
  assert.deepEqual(second.candidates.map((item) => item.external_id), ["102", "101"]);
  assert.equal(second.done, false);
  const third = await discover({ source: "newspim", maxUrls: 2, cursor: second.next_cursor, ...network.options });
  assert.deepEqual(third.candidates.map((item) => item.external_id), ["100"]);
  assert.equal(third.done, true);
  assert.equal(third.next_cursor, null);
  for (let index = 1; index < network.requested.length; index += 1) assert.ok(network.requested[index].at - network.requested[index - 1].at >= 1_200);
});

test("page budget preserves nested sitemap traversal for the next call", async () => {
  clearRobotsPolicyCache();
  const network = fakeNetwork({
    "https://www.mdtoday.co.kr/sitemap.xml": `<sitemapindex><sitemap><loc>https://www.mdtoday.co.kr/one.xml</loc></sitemap></sitemapindex>`,
    "https://www.mdtoday.co.kr/one.xml": `<urlset><url><loc>https://www.mdtoday.co.kr/news/articleView.html?idxno=20</loc></url></urlset>`,
  });
  const first = await discover({ source: "mdtoday", maxPages: 1, ...network.options });
  assert.equal(first.candidates.length, 0);
  assert.equal(first.done, false);
  const second = await discover({ source: "mdtoday", maxPages: 1, cursor: first.next_cursor, ...network.options });
  assert.equal(second.candidates[0].external_id, "20");
  assert.equal(second.done, true);
});

test("new articles inserted at the front of a sitemap do not move the resume anchor", async () => {
  clearRobotsPolicyCache();
  let ids = [104, 103, 102, 101];
  const network = fakeNetwork({
    "https://www.newspim.com/sitemap/recent/all_1": () => new Response(sitemap(ids)),
    "https://www.newspim.com/sitemap/recent/all_2": sitemap([]),
  });
  const first = await discover({ source: "newspim", maxUrls: 2, ...network.options });
  ids = [106, 105, ...ids];
  const resumed = await discover({ source: "newspim", maxUrls: 10, cursor: first.next_cursor, ...network.options });
  assert.deepEqual(resumed.candidates.map((item) => item.external_id), ["102", "101"]);
  const fresh = await discover({ source: "newspim", maxUrls: 2, ...network.options });
  assert.deepEqual(fresh.candidates.map((item) => item.external_id), ["106", "105"]);
});

test("HTTP failure retains the caller's cursor and oversized documents fail explicitly", async () => {
  clearRobotsPolicyCache();
  const network = fakeNetwork({ "https://www.sedaily.com/sitemap/latestnews": () => new Response("unavailable", { status: 503 }) });
  await assert.rejects(discover({ source: "sedaily", ...network.options }), /discovery_fetch_http_503/);
  const oversized = fakeNetwork({ "https://www.sedaily.com/sitemap/latestnews": () => new Response("too big", { headers: { "content-length": "5000001" } }) });
  await assert.rejects(discover({ source: "sedaily", ...oversized.options }), /discovery_response_too_large/);
});

test("Hellot bounded historical cursor has no MySQL or network dependency", async () => {
  const first = await discover({ source: "hellot", minId: 100, maxId: 102, maxUrls: 2 });
  assert.deepEqual(first.candidates.map((item) => item.external_id), ["100", "101"]);
  const second = await discover({ source: "hellot", minId: 100, maxId: 102, maxUrls: 2, cursor: first.next_cursor });
  assert.deepEqual(second.candidates.map((item) => item.external_id), ["102"]);
  assert.equal(second.done, true);
  await assert.rejects(discover({ source: "hellot", minId: 100, maxId: 103, cursor: first.next_cursor }), /cursor_range_mismatch/);
});

test("article fetch preserves body/robots/source and never invents unknown publication dates", async () => {
  clearRobotsPolicyCache();
  const url = "https://www.hellot.net/news/article.html?no=100";
  const body = "실제 수집 테스트를 위한 국내 뉴스 본문입니다. ".repeat(12);
  const html = `<html><head><script type="application/ld+json">${JSON.stringify({ "@type": "NewsArticle", headline: "[헬로티 HelloT] 산업 뉴스", articleBody: body, publisher: { name: "헬로티" } })}</script></head><body></body></html>`;
  const network = fakeNetwork({ [url]: () => new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } }) });
  const result = await fetchArticle({ source: "hellot", url, ...network.options });
  assert.equal(result.title, "산업 뉴스");
  assert.equal(result.region, "domestic");
  assert.equal(result.language, "ko");
  assert.equal(result.published_at, null);
  assert.equal(result.robots.status, "allowed");
  assert.match(result.content, /국내 뉴스/);
  await assert.rejects(fetchArticle({ source: "hellot", url: "http://127.0.0.1/news/article.html?no=100", ...network.options }), /private_host/);
});

test("robots rejection and failed discovery are surfaced instead of advancing cursor", async () => {
  clearRobotsPolicyCache();
  let pageRequests = 0;
  await assert.rejects(discover({ source: "sedaily", sleep: async () => {}, fetchImpl: async (url) => {
    if (String(url).endsWith("/robots.txt")) return new Response("User-agent: *\nDisallow: /\n");
    pageRequests += 1;
    return new Response("unused");
  } }), /article_robots_disallowed/);
  assert.equal(pageRequests, 0);
});

test("CLI rejects unknown modes and out of range limits", async () => {
  assert.throws(() => parseCliArgs(["--mode", "dump", "--source", "hellot"]), /invalid_mode/);
  assert.throws(() => parseCliArgs(["--mode", "fetch", "--source", "hellot"]), /missing_url/);
  await assert.rejects(discover({ source: "hellot", maxUrls: 1001 }), /invalid_max_urls/);
});
