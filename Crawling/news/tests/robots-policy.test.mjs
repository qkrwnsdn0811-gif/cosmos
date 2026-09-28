import assert from "node:assert/strict";
import test from "node:test";
import { extractNewsArticle } from "../lib/news-article-extractor.ts";
import {
  assertRobotsAllowed,
  clearRobotsPolicyCache,
  isRobotsPathAllowed,
} from "../lib/robots-policy.ts";

const userAgent = "NexusRiskArticleExtractor/1.0 (+news intelligence; source-linked)";

test("applies the most specific matching rule and lets Allow win a tie", () => {
  const robots = `
User-agent: *
Disallow: /

User-agent: NexusRiskArticleExtractor
Disallow: /news/
Allow: /news/public/
Disallow: /news/public/private$
Allow: /news/public/private$
`;

  assert.equal(isRobotsPathAllowed(robots, "https://media.example.com/news/blocked", userAgent), false);
  assert.equal(isRobotsPathAllowed(robots, "https://media.example.com/news/public/story", userAgent), true);
  assert.equal(isRobotsPathAllowed(robots, "https://media.example.com/news/public/private", userAgent), true);
  assert.equal(isRobotsPathAllowed(robots, "https://media.example.com/about", userAgent), true);
});

test("uses wildcard rules only when no product-token group matches", () => {
  const robots = `
User-agent: *
Disallow: /

User-agent: AnotherBot
Allow: /
`;
  assert.equal(isRobotsPathAllowed(robots, "https://media.example.com/article/1", userAgent), false);
});

test("allows access when robots.txt is absent and caches the result", async () => {
  clearRobotsPolicyCache();
  let calls = 0;
  const fetchImpl = async (url) => {
    calls += 1;
    assert.equal(url, "https://missing.example.com/robots.txt");
    return new Response("not found", { status: 404 });
  };

  const first = await assertRobotsAllowed("https://missing.example.com/article/1", { fetchImpl, userAgent });
  const second = await assertRobotsAllowed("https://missing.example.com/article/2", { fetchImpl, userAgent });
  assert.equal(first.status, "allowed");
  assert.equal(second.status, "allowed");
  assert.equal(calls, 1);
});

test("fails closed when robots.txt cannot be reached", async () => {
  clearRobotsPolicyCache();
  await assert.rejects(
    assertRobotsAllowed("https://unavailable.example.com/article/1", {
      fetchImpl: async () => new Response("server error", { status: 503 }),
      userAgent,
    }),
    /article_robots_unavailable/,
  );
});

test("does not request article HTML when robots.txt disallows the URL", async () => {
  clearRobotsPolicyCache();
  const requested = [];
  const fetchImpl = async (url) => {
    requested.push(url);
    return new Response("User-agent: *\nDisallow: /article/", {
      status: 200,
      headers: { "content-type": "text/plain" },
    });
  };

  await assert.rejects(
    extractNewsArticle("https://blocked.example.com/article/1", { fetchImpl, userAgent }),
    /article_robots_disallowed/,
  );
  assert.deepEqual(requested, ["https://blocked.example.com/robots.txt"]);
});

test("checks the destination host robots.txt before following an article redirect", async () => {
  clearRobotsPolicyCache();
  const requested = [];
  const fetchImpl = async (url) => {
    requested.push(url);
    if (url === "https://origin.example.com/robots.txt") {
      return new Response("User-agent: *\nAllow: /", { status: 200 });
    }
    if (url === "https://origin.example.com/start") {
      return new Response(null, { status: 302, headers: { location: "https://target.example.com/article/1" } });
    }
    if (url === "https://target.example.com/robots.txt") {
      return new Response("User-agent: *\nDisallow: /", { status: 200 });
    }
    throw new Error(`unexpected_url:${url}`);
  };

  await assert.rejects(
    extractNewsArticle("https://origin.example.com/start", { fetchImpl, userAgent }),
    /article_robots_disallowed/,
  );
  assert.deepEqual(requested, [
    "https://origin.example.com/robots.txt",
    "https://origin.example.com/start",
    "https://target.example.com/robots.txt",
  ]);
});
