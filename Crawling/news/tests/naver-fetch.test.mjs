import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, symlinkSync, unlinkSync, rmdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { fetchNaverArticle, assertArticleBody } from "../services/news_pipeline/naver_fetch.mjs";

test("CLI executes through the deployment directory symlink", () => {
  const temporary = mkdtempSync(join(tmpdir(), "cosmos-naver-cli-"));
  const link = join(temporary, "app");
  try {
    symlinkSync(fileURLToPath(new URL("../services/news_pipeline/", import.meta.url)), link,
      process.platform === "win32" ? "junction" : "dir");
    // Invalid arguments exercise the CLI entry point without a network request.
    const result = spawnSync(process.execPath, ["--experimental-strip-types", join(link, "naver_fetch.mjs")],
      { encoding: "utf8", timeout: 10_000 });
    assert.equal(result.status, 1, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), { status: "error", error: "invalid_arguments" });
  } finally {
    try { unlinkSync(link); } catch (error) { if (error.code !== "ENOENT") throw error; }
    rmdirSync(temporary);
  }
});

test("Naver-discovered URL uses full article extraction and no API authentication headers", async () => {
  const body = "삼성전자가 공급망 투자를 확대한다는 기사 본문입니다. ".repeat(12);
  const html = `<html><head><script type="application/ld+json">${JSON.stringify({ "@type": "NewsArticle", headline: "삼성전자 투자", articleBody: body })}</script></head><body><article>${body}</article></body></html>`;
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url: String(url), headers: options.headers });
    return String(url).endsWith("/robots.txt")
      ? new Response("User-agent: *\nAllow: /", { headers: { "content-type": "text/plain" } })
      : new Response(html, { headers: { "content-type": "text/html" } });
  };
  const result = await fetchNaverArticle("https://publisher.example.com/news/1", { fetchImpl });
  assert.equal(result.content, body.trim());
  assert.equal(result.extraction_method, "json-ld");
  assert.ok(calls.every(call => !Object.keys(call.headers).some(name => /client|secret|api-key/i.test(name))));
});

test("Naver adapter rejects private URLs before fetching", async () => {
  await assert.rejects(fetchNaverArticle("http://127.0.0.1/private", {
    fetchImpl: async () => { throw new Error("must not fetch"); },
  }));
});

test("HTTP 200 interstitials and gated excerpts are not full articles", () => {
  assert.throws(() => assertArticleBody({ title: "Access required", textContent: "This service is temporarily unavailable. Please enable cookies and sign in to continue. ".repeat(6) }), /interstitial/);
  assert.throws(() => assertArticleBody({ title: "삼성전자 뉴스", textContent: "삼성전자 뉴스의 일부입니다. 전체 기사를 보려면 구독 후 로그인하세요.".repeat(3) }), /interstitial/);
  assert.doesNotThrow(() => assertArticleBody({ title: "삼성전자 보안 기능 개선", textContent: "삼성전자가 인증 기능을 개선했다. ".repeat(30) }));
});

test("redirect to IPv4-mapped IPv6 loopback is rejected before any internal request", async () => {
  const calls = [];
  const fetchImpl = async (url) => {
    calls.push(String(url));
    if (String(url).endsWith("/robots.txt")) return new Response("User-agent: *\nAllow: /");
    return new Response(null, { status: 302, headers: { location: "http://[::ffff:127.0.0.1]/article" } });
  };
  await assert.rejects(fetchNaverArticle("https://redirect.example.com/news/1", { fetchImpl }), /private_host/);
  assert.ok(calls.every(url => url.startsWith("https://redirect.example.com/")));
});
