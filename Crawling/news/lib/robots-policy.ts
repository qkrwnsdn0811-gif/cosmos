import { validatePublicHttpUrl } from "./public-http-url.ts";

const DEFAULT_CACHE_TTL_MS = 60 * 60 * 1_000;
const FAILURE_CACHE_TTL_MS = 5 * 60 * 1_000;
const MAX_CACHE_TTL_MS = 24 * 60 * 60 * 1_000;
const DEFAULT_MAX_RESPONSE_BYTES = 512_000;
const DEFAULT_MAX_REDIRECTS = 5;

type RobotsRule = {
  directive: "allow" | "disallow";
  pattern: string;
};

type RobotsGroup = {
  agents: string[];
  rules: RobotsRule[];
};

type CachedRobotsPolicy = {
  expiresAt: number;
  groups?: RobotsGroup[];
  access: "rules" | "allow-all" | "disallow-all";
  policyUrl: string;
  reason: string;
};

export type RobotsAccessRecord = {
  status: "allowed";
  checkedAt: string;
  policyUrl: string;
  userAgent: string;
};

export type RobotsPolicyOptions = {
  fetchImpl?: typeof fetch;
  signal?: AbortSignal;
  userAgent?: string;
  cacheTtlMs?: number;
  maxResponseBytes?: number;
  maxRedirects?: number;
  now?: () => number;
};

const policyCache = new Map<string, CachedRobotsPolicy>();
const pendingPolicies = new Map<string, Promise<CachedRobotsPolicy>>();

export async function assertRobotsAllowed(
  inputUrl: string,
  options: RobotsPolicyOptions = {},
): Promise<RobotsAccessRecord> {
  const articleUrl = validatePublicHttpUrl(inputUrl, "article");
  const userAgent = options.userAgent ?? "NexusRiskArticleExtractor/1.0 (+news intelligence; source-linked)";
  const productToken = robotsProductToken(userAgent);
  const policy = await policyForOrigin(articleUrl, productToken, userAgent, options);
  const allowed = policy.access === "allow-all"
    || (policy.access === "rules" && isPathAllowed(policy.groups ?? [], articleUrl, productToken));

  if (!allowed) {
    throw new Error(policy.access === "disallow-all"
      ? "article_robots_unavailable"
      : "article_robots_disallowed");
  }
  return {
    status: "allowed",
    checkedAt: new Date((options.now ?? Date.now)()).toISOString(),
    policyUrl: policy.policyUrl,
    userAgent: productToken,
  };
}

export function isRobotsPathAllowed(robotsText: string, inputUrl: string, userAgent: string): boolean {
  const url = validatePublicHttpUrl(inputUrl, "article");
  return isPathAllowed(parseRobotsTxt(robotsText), url, robotsProductToken(userAgent));
}

export function clearRobotsPolicyCache(): void {
  policyCache.clear();
  pendingPolicies.clear();
}

async function policyForOrigin(
  articleUrl: URL,
  productToken: string,
  userAgent: string,
  options: RobotsPolicyOptions,
): Promise<CachedRobotsPolicy> {
  const now = (options.now ?? Date.now)();
  const key = `${articleUrl.origin}|${productToken.toLowerCase()}`;
  const cached = policyCache.get(key);
  if (cached && cached.expiresAt > now) return cached;

  const pending = pendingPolicies.get(key);
  if (pending) return pending;

  const request = fetchRobotsPolicy(articleUrl, userAgent, options, now)
    .then((policy) => {
      policyCache.set(key, policy);
      return policy;
    })
    .finally(() => pendingPolicies.delete(key));
  pendingPolicies.set(key, request);
  return request;
}

async function fetchRobotsPolicy(
  articleUrl: URL,
  userAgent: string,
  options: RobotsPolicyOptions,
  now: number,
): Promise<CachedRobotsPolicy> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const maxRedirects = positiveInteger(options.maxRedirects, DEFAULT_MAX_REDIRECTS);
  const maxResponseBytes = Math.max(DEFAULT_MAX_RESPONSE_BYTES, positiveInteger(options.maxResponseBytes, DEFAULT_MAX_RESPONSE_BYTES));
  const configuredTtl = clampTtl(options.cacheTtlMs ?? DEFAULT_CACHE_TTL_MS);
  const originalPolicyUrl = new URL("/robots.txt", articleUrl.origin).toString();
  let currentPolicyUrl = originalPolicyUrl;

  try {
    for (let redirectCount = 0; redirectCount <= maxRedirects; redirectCount += 1) {
      const response = await fetchImpl(currentPolicyUrl, {
        method: "GET",
        redirect: "manual",
        signal: options.signal,
        headers: {
          accept: "text/plain,*/*;q=0.1",
          "user-agent": userAgent,
        },
      });

      if (isRedirect(response.status)) {
        const location = response.headers.get("location");
        await response.body?.cancel();
        if (!location || redirectCount === maxRedirects) {
          return disallowAll(originalPolicyUrl, "redirect_error", now);
        }
        currentPolicyUrl = validatePublicHttpUrl(new URL(location, currentPolicyUrl).toString(), "robots").toString();
        continue;
      }

      const responseTtl = cacheTtlFromHeaders(response.headers, configuredTtl);
      if (response.status >= 200 && response.status < 300) {
        const robotsText = await readLimitedText(response, maxResponseBytes);
        return {
          access: "rules",
          groups: parseRobotsTxt(robotsText),
          policyUrl: originalPolicyUrl,
          reason: "robots_rules",
          expiresAt: now + responseTtl,
        };
      }

      await response.body?.cancel();
      if (response.status === 401 || response.status === 403 || response.status === 429 || response.status >= 500) {
        return disallowAll(originalPolicyUrl, `http_${response.status}`, now);
      }
      if (response.status >= 400 && response.status < 500) {
        return {
          access: "allow-all",
          policyUrl: originalPolicyUrl,
          reason: `http_${response.status}`,
          expiresAt: now + responseTtl,
        };
      }
      return disallowAll(originalPolicyUrl, `http_${response.status}`, now);
    }
  } catch {
    return disallowAll(originalPolicyUrl, "request_failed", now);
  }

  return disallowAll(originalPolicyUrl, "redirect_error", now);
}

function disallowAll(policyUrl: string, reason: string, now: number): CachedRobotsPolicy {
  return {
    access: "disallow-all",
    policyUrl,
    reason,
    expiresAt: now + FAILURE_CACHE_TTL_MS,
  };
}

function parseRobotsTxt(value: string): RobotsGroup[] {
  const groups: RobotsGroup[] = [];
  let agents: string[] = [];
  let rules: RobotsRule[] = [];

  const flush = () => {
    if (agents.length) groups.push({ agents, rules });
    agents = [];
    rules = [];
  };

  for (const rawLine of value.replace(/^\uFEFF/, "").split(/\r?\n/)) {
    const line = rawLine.replace(/#.*$/, "").trim();
    if (!line) {
      if (agents.length && rules.length) flush();
      continue;
    }
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    const field = line.slice(0, separator).trim().toLowerCase();
    const content = line.slice(separator + 1).trim();

    if (field === "user-agent") {
      if (!content) continue;
      if (rules.length) flush();
      agents.push(content.toLowerCase());
      continue;
    }
    if ((field === "allow" || field === "disallow") && agents.length && content) {
      rules.push({ directive: field, pattern: content });
    }
  }
  flush();
  return groups;
}

function isPathAllowed(groups: RobotsGroup[], url: URL, productToken: string): boolean {
  const normalizedToken = productToken.toLowerCase();
  const specificGroups = groups.filter((group) => group.agents.includes(normalizedToken));
  const selected = specificGroups.length
    ? specificGroups
    : groups.filter((group) => group.agents.includes("*"));
  if (!selected.length) return true;

  const path = `${url.pathname}${url.search}` || "/";
  const matches = selected
    .flatMap((group) => group.rules)
    .filter((rule) => robotsPatternMatches(rule.pattern, path))
    .map((rule) => ({ ...rule, specificity: rule.pattern.replace(/\*|\$$/g, "").length }));
  if (!matches.length) return true;
  matches.sort((left, right) => right.specificity - left.specificity
    || (left.directive === "allow" ? -1 : 1));
  return matches[0].directive === "allow";
}

function robotsPatternMatches(pattern: string, path: string): boolean {
  const endAnchored = pattern.endsWith("$");
  const rawPattern = endAnchored ? pattern.slice(0, -1) : pattern;
  const expression = rawPattern
    .split("*")
    .map((part) => escapeRegExp(part))
    .join(".*");
  try {
    return new RegExp(`^${expression}${endAnchored ? "$" : ""}`, "u").test(path);
  } catch {
    return false;
  }
}

function robotsProductToken(userAgent: string): string {
  return userAgent.match(/[A-Za-z_-][A-Za-z0-9_-]*/)?.[0] ?? "NexusRiskArticleExtractor";
}

function cacheTtlFromHeaders(headers: Headers, fallback: number): number {
  const cacheControl = headers.get("cache-control") ?? "";
  const maxAgeSeconds = Number(cacheControl.match(/(?:^|,)\s*max-age\s*=\s*(\d+)/i)?.[1]);
  return Number.isFinite(maxAgeSeconds) ? clampTtl(maxAgeSeconds * 1_000) : fallback;
}

function clampTtl(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_CACHE_TTL_MS;
  return Math.min(MAX_CACHE_TTL_MS, Math.max(60_000, Math.trunc(value)));
}

function positiveInteger(value: number | undefined, fallback: number): number {
  return Number.isInteger(value) && (value ?? 0) > 0 ? value! : fallback;
}

function isRedirect(status: number): boolean {
  return status === 301 || status === 302 || status === 303 || status === 307 || status === 308;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function readLimitedText(response: Response, maxBytes: number): Promise<string> {
  if (!response.body) return "";
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (total < maxBytes) {
    const { done, value } = await reader.read();
    if (done) break;
    const remaining = maxBytes - total;
    const chunk = value.byteLength > remaining ? value.slice(0, remaining) : value;
    chunks.push(chunk);
    total += chunk.byteLength;
    if (chunk.byteLength < value.byteLength) {
      await reader.cancel();
      break;
    }
  }
  if (total >= maxBytes) await reader.cancel().catch(() => undefined);

  const output = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    output.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: false }).decode(output);
}
