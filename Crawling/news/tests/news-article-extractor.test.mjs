import assert from "node:assert/strict";
import test from "node:test";
import { extractNewsArticleFromHtml, validatePublicArticleUrl } from "../lib/news-article-extractor.ts";

test("extracts articleBody and metadata from JSON-LD", () => {
  const body = "첫 번째 문단입니다. ".repeat(20) + "두 번째 문단입니다. ".repeat(20);
  const html = `<!doctype html><html><head><title>fallback</title><script type="application/ld+json">${JSON.stringify({
    "@type": "NewsArticle",
    headline: "테스트 뉴스 제목",
    datePublished: "2026-09-01T10:00:00+09:00",
    author: { name: "테스트 기자" },
    publisher: { name: "테스트 뉴스" },
    articleBody: body,
  })}</script></head><body><nav>메뉴 메뉴 메뉴</nav></body></html>`;

  const article = extractNewsArticleFromHtml(html, "https://news.example.com/articles/1");
  assert.equal(article.method, "json-ld");
  assert.equal(article.title, "테스트 뉴스 제목");
  assert.equal(article.byline, "테스트 기자");
  assert.equal(article.siteName, "테스트 뉴스");
  assert.ok(article.charCount >= 400);
  assert.doesNotMatch(article.textContent, /메뉴/);
});

test("falls back to Readability for semantic article HTML", () => {
  const paragraphs = Array.from({ length: 8 }, (_, index) =>
    `<p>${index + 1}번째 문단은 실제 뉴스 본문을 흉내 낸 충분히 긴 테스트 문장입니다. 기업의 공급망 변화와 생산 계획에 관한 구체적인 내용을 설명합니다.</p>`,
  ).join("");
  const html = `<!doctype html><html><head><title>공급망 테스트 기사</title></head><body><header>사이트 메뉴</header><article><h1>공급망 테스트 기사</h1>${paragraphs}</article><footer>푸터</footer></body></html>`;

  const article = extractNewsArticleFromHtml(html, "https://news.example.com/articles/2");
  assert.equal(article.method, "readability");
  assert.match(article.textContent, /기업의 공급망 변화/);
  assert.doesNotMatch(article.textContent, /사이트 메뉴|푸터/);
  assert.ok(article.charCount >= 500);
});

test("extracts Naver's dic_area article body and preserves line breaks", () => {
  const paragraphs = Array.from({ length: 10 }, (_, index) =>
    `${index + 1}번째 네이버 뉴스 본문 문단입니다. 반도체 공급망과 기업 생산 계획을 설명하는 충분히 긴 테스트 문장입니다.`,
  );
  const html = `<!doctype html><html><head><meta property="og:title" content="네이버 뉴스 테스트"></head><body>
    <nav>네이버 뉴스 메뉴</nav>
    <div id="dic_area">${paragraphs.join("<br><br>")}<div class="reporter_area">기자 정보 영역</div></div>
  </body></html>`;

  const article = extractNewsArticleFromHtml(html, "https://n.news.naver.com/mnews/article/001/0000000000");
  assert.match(article.textContent, /1번째 네이버 뉴스 본문 문단/);
  assert.match(article.textContent, /10번째 네이버 뉴스 본문 문단/);
  assert.doesNotMatch(article.textContent, /기자 정보 영역|네이버 뉴스 메뉴/);
  assert.ok(article.textContent.includes("\n\n"));
});

test("extracts legacy HelloT news_body_area content", () => {
  const paragraphs = Array.from({ length: 6 }, (_, index) =>
    `<p>${index + 1}번째 헬로티 과거 기사 본문입니다. 산업 자동화 전시회와 스마트 제품의 주요 특징을 설명하는 충분히 긴 문장입니다.</p>`,
  ).join("");
  const html = `<!doctype html><html><head><meta property="og:title" content="과거 헬로티 기사"></head><body>
    <div class="layout"><div class="news_body_area">${paragraphs}</div></div>
  </body></html>`;

  const article = extractNewsArticleFromHtml(html, "https://www.hellot.net/news/article.html?no=10000");
  assert.ok(["readability", "article-selector"].includes(article.method));
  assert.match(article.textContent, /산업 자동화 전시회/);
  assert.ok(article.charCount >= 400);
});

test("recognizes HelloT's HTTP 200 missing-article page", () => {
  const html = `<meta charset="utf-8"><script>window.alert('기사번호가 누락되어 있거나 잘못된 접근입니다.'); window.location='/';</script>`;
  assert.throws(
    () => extractNewsArticleFromHtml(html, "https://www.hellot.net/news/article.html?no=10005"),
    /article_not_found/,
  );
});

test("rejects local and private-network article URLs", () => {
  for (const url of ["http://localhost/news", "http://127.0.0.1/news", "http://10.0.0.4/news", "file:///tmp/news.html"]) {
    assert.throws(() => validatePublicArticleUrl(url));
  }
  assert.equal(validatePublicArticleUrl("https://news.example.com/article").hostname, "news.example.com");
});
