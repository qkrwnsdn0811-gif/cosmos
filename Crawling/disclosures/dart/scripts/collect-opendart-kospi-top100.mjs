#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";
import { parseHTML } from "linkedom";

const DART_BASE = "https://opendart.fss.or.kr/api";
const KRX_CORP_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download";
const NAVER_CAP_URL = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0&page=";
const GUIDE_URL = "https://opendart.fss.or.kr/guide";
const FATAL_DART_STATUSES = new Set(["010", "011", "012", "020", "901"]);
const REPORT_PERIODS = [
  { key: "2025_annual", year: "2025", code: "11011", marker: "2025.12", label: "사업보고서" },
  { key: "2026_q1", year: "2026", code: "11013", marker: "2026.03", label: "분기보고서" },
  { key: "2026_half", year: "2026", code: "11012", marker: "2026.06", label: "반기보고서" },
];
const RATIO_CATEGORIES = ["M210000", "M220000", "M230000", "M240000"];
const INTERIM_PERIODIC = new Set([
  "stockTotqySttus", "tesstkAcqsDspsSttus", "alotMatter", "accnutAdtorNmNdAdtOpinion",
  "hyslrSttus", "hyslrChgSttus", "mrhlSttus", "exctvSttus", "empSttus", "otrCprInvstmntSttus",
]);

export function parseArgs(argv) {
  const result = { asOf: new Date().toISOString().slice(0, 10), concurrency: 4, universeOnly: false, output: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--universe-only") result.universeOnly = true;
    else if (argv[i] === "--as-of") result.asOf = argv[++i];
    else if (argv[i] === "--concurrency") result.concurrency = Number(argv[++i]);
    else if (argv[i] === "--output") result.output = argv[++i];
    else if (argv[i] === "--help") result.help = true;
    else throw new Error(`알 수 없는 옵션: ${argv[i]}`);
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(result.asOf)) throw new Error("--as-of는 YYYY-MM-DD 형식이어야 합니다.");
  if (!Number.isInteger(result.concurrency) || result.concurrency < 1 || result.concurrency > 10) {
    throw new Error("--concurrency는 1~10 정수여야 합니다.");
  }
  return result;
}

export function isCommonShareName(name) {
  const n = String(name ?? "").trim();
  if (!n) return false;
  if (/(스팩|ETF|ETN|인버스|레버리지|선물|채권|액티브)/i.test(n)) return false;
  if (/우(?:B|C|선주|\(전환\))?$/.test(n) || /\d우[BC]?$/.test(n)) return false;
  return true;
}

function cleanText(value) {
  return String(value ?? "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function cleanNumber(value) {
  const n = cleanText(value).replace(/[,%]/g, "");
  return n === "" || n === "-" ? null : Number(n);
}

export function parseNaverMarketCapPage(html) {
  const { document } = parseHTML(html);
  const rows = [];
  for (const tr of document.querySelectorAll("table.type_2 tr")) {
    const cells = [...tr.querySelectorAll("td")].map((td) => cleanText(td.textContent));
    const link = tr.querySelector("a.tltle");
    if (!link || cells.length < 10) continue;
    const code = new URL(link.getAttribute("href"), "https://finance.naver.com").searchParams.get("code");
    if (!/^\d{6}$/.test(code ?? "")) continue;
    rows.push({
      market_cap_rank: cleanNumber(cells[0]), stock_name: cleanText(link.textContent), stock_code: code,
      close_price_krw: cleanNumber(cells[2]), change_krw: cleanNumber(cells[3]), change_pct: cleanNumber(cells[4]),
      par_value_krw: cleanNumber(cells[5]), market_cap_100m_krw: cleanNumber(cells[6]),
      listed_shares_thousand: cleanNumber(cells[7]), foreign_ownership_pct: cleanNumber(cells[8]),
      volume: cleanNumber(cells[9]), per: cleanNumber(cells[10]), roe: cleanNumber(cells[11]),
    });
  }
  return rows;
}

export function parseKrxRegistry(html) {
  const { document } = parseHTML(html);
  const result = [];
  for (const tr of document.querySelectorAll("table tr")) {
    const cells = [...tr.querySelectorAll("td")].map((td) => cleanText(td.textContent));
    if (cells.length < 7) continue;
    const code = cells[2];
    if (!/^\d{6}$/.test(code) || !/^(유가|유가증권|코스피)$/.test(cells[1])) continue;
    result.push({ corp_name: cells[0], market: cells[1], stock_code: code, industry: cells[3] ?? "", products: cells[4] ?? "", listing_date: cells[5] ?? "" });
  }
  return result;
}

export function parseCorpCodeXml(xml) {
  const { document } = parseHTML(xml);
  return [...document.querySelectorAll("list")].map((node) => ({
    corp_code: cleanText(node.querySelector("corp_code")?.textContent),
    corp_name: cleanText(node.querySelector("corp_name")?.textContent),
    stock_code: cleanText(node.querySelector("stock_code")?.textContent),
    modify_date: cleanText(node.querySelector("modify_date")?.textContent),
  })).filter((row) => row.corp_code);
}

export function attachCorpCodes(universe, corporations) {
  const byStock = new Map(corporations.filter((row) => row.stock_code).map((row) => [row.stock_code, row]));
  for (const company of universe) {
    const corp = byStock.get(company.stock_code);
    if (!corp || !/^\d{8}$/.test(corp.corp_code)) {
      throw new Error(`OpenDART 고유번호 매핑 실패: ${company.stock_code} ${company.stock_name}`);
    }
    company.corp_code = corp.corp_code;
  }
  return universe;
}

export function extractFirstZipEntry(buffer) {
  const b = Buffer.from(buffer);
  const eocd = b.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
  if (eocd < 0) throw new Error("ZIP 중앙 디렉터리를 찾지 못했습니다.");
  const centralOffset = b.readUInt32LE(eocd + 16);
  if (b.readUInt32LE(centralOffset) !== 0x02014b50) throw new Error("ZIP 중앙 디렉터리가 손상되었습니다.");
  const method = b.readUInt16LE(centralOffset + 10);
  const compressedSize = b.readUInt32LE(centralOffset + 20);
  const uncompressedSize = b.readUInt32LE(centralOffset + 24);
  const localOffset = b.readUInt32LE(centralOffset + 42);
  if (b.readUInt32LE(localOffset) !== 0x04034b50) throw new Error("ZIP 로컬 헤더가 손상되었습니다.");
  const nameLength = b.readUInt16LE(localOffset + 26);
  const extraLength = b.readUInt16LE(localOffset + 28);
  const start = localOffset + 30 + nameLength + extraLength;
  const compressed = b.subarray(start, start + compressedSize);
  const data = method === 0 ? compressed : method === 8 ? inflateRawSync(compressed) : null;
  if (!data) throw new Error(`지원하지 않는 ZIP 압축 방식: ${method}`);
  if (data.length !== uncompressedSize) throw new Error("ZIP 압축 해제 크기가 일치하지 않습니다.");
  return data;
}

export function rowsToCsv(rows) {
  if (!rows.length) return "";
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  const quote = (value) => {
    if (value === null || value === undefined) return "";
    const text = typeof value === "object" ? JSON.stringify(value) : String(value);
    return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  return `${columns.map(quote).join(",")}\r\n${rows.map((row) => columns.map((c) => quote(row[c])).join(",")).join("\r\n")}\r\n`;
}

async function fetchBuffer(url, options = {}, tries = 4) {
  let lastError;
  for (let attempt = 1; attempt <= tries; attempt += 1) {
    try {
      const response = await fetch(url, { ...options, signal: AbortSignal.timeout(90_000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return Buffer.from(await response.arrayBuffer());
    } catch (error) {
      lastError = error;
      if (attempt < tries) await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** (attempt - 1)));
    }
  }
  throw lastError;
}

async function fetchText(url, encoding = "utf-8") {
  const bytes = await fetchBuffer(url);
  return new TextDecoder(encoding).decode(bytes);
}

async function writeJson(file, value) {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

async function writeRows(base, name, rows) {
  const safe = name.replace(/[^A-Za-z0-9_.-]+/g, "_");
  await mkdir(base, { recursive: true });
  await Promise.all([
    writeFile(path.join(base, `${safe}.csv`), rowsToCsv(rows), "utf8"),
    writeFile(path.join(base, `${safe}.jsonl`), rows.map((row) => JSON.stringify(row)).join("\n") + (rows.length ? "\n" : ""), "utf8"),
  ]);
}

function readDotEnv(text) {
  const env = {};
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
    if (match) env[match[1]] = match[2].replace(/^(['"])(.*)\1$/, "$2");
  }
  return env;
}

async function loadApiKey() {
  if (process.env.DART_API_KEY) return process.env.DART_API_KEY.trim();
  for (const file of [".env.dart.local", ".env.local", ".env"]) {
    if (!existsSync(file)) continue;
    const value = readDotEnv(await readFile(file, "utf8")).DART_API_KEY?.trim();
    if (value) return value;
  }
  throw new Error("DART_API_KEY가 없습니다. .env.dart.local 또는 환경 변수에 설정하세요.");
}

async function buildUniverse(outputDir, asOf) {
  const sourceDir = path.join(outputDir, "raw", "universe_sources");
  await mkdir(sourceDir, { recursive: true });
  const krxBytes = await fetchBuffer(KRX_CORP_URL);
  const krxHtml = new TextDecoder("euc-kr").decode(krxBytes);
  await writeFile(path.join(sourceDir, "krx_kind_kospi_company_list.html"), krxBytes);
  const registry = parseKrxRegistry(krxHtml);
  const registryCodes = new Set(registry.map((row) => row.stock_code));
  const registryMap = new Map(registry.map((row) => [row.stock_code, row]));
  const ranked = [];
  for (let page = 1; page <= 5 && ranked.length < 100; page += 1) {
    const html = await fetchText(`${NAVER_CAP_URL}${page}`, "euc-kr");
    await writeFile(path.join(sourceDir, `naver_kospi_market_cap_page_${page}.html`), html, "utf8");
    for (const row of parseNaverMarketCapPage(html)) {
      if (!registryCodes.has(row.stock_code) || !isCommonShareName(row.stock_name)) continue;
      if (ranked.some((item) => item.stock_code === row.stock_code)) continue;
      ranked.push({ ...row, ...registryMap.get(row.stock_code), snapshot_date: asOf, universe_rule: "KOSPI 보통주 시가총액 상위 100" });
      if (ranked.length === 100) break;
    }
  }
  if (ranked.length !== 100) throw new Error(`유니버스가 100개가 아닙니다: ${ranked.length}`);
  ranked.forEach((row, i) => { row.universe_rank = i + 1; });
  await writeRows(path.join(outputDir, "normalized"), "universe", ranked);
  await writeJson(path.join(outputDir, "metadata", "universe_definition.json"), {
    snapshot_date: asOf,
    definition: "KRX KIND 유가증권시장 상장법인 목록과 네이버 금융 KOSPI 시가총액 순위를 교차해 보통주 상위 100개를 선정",
    exclusions: "우선주, ETF, ETN, 스팩 및 기타 펀드성 종목",
    sources: [KRX_CORP_URL, `${NAVER_CAP_URL}1`],
    count: ranked.length,
  });
  return ranked;
}

function endpointStem(endpoint) {
  return endpoint.replace(/\.(json|xml)$/i, "");
}

async function discoverEndpoints(group, outputDir) {
  const mainUrl = `${GUIDE_URL}/main.do?apiGrpCd=${group}`;
  const html = await fetchText(mainUrl);
  const ids = [...new Set([...html.matchAll(new RegExp(`detail\\.do\\?apiGrpCd=${group}(?:&|&amp;)apiId=(\\d+)`, "g"))].map((m) => m[1]))];
  const endpoints = [];
  for (const id of ids) {
    const detailUrl = `${GUIDE_URL}/detail.do?apiGrpCd=${group}&apiId=${id}`;
    const detail = await fetchText(detailUrl);
    const match = detail.match(/https:\/\/opendart\.fss\.or\.kr\/api\/([A-Za-z0-9]+\.(?:json|xml))/);
    if (match) endpoints.push({ group, api_id: id, endpoint: match[1], guide_url: detailUrl });
  }
  const deduped = [...new Map(endpoints.map((item) => [item.endpoint, item])).values()];
  await writeJson(path.join(outputDir, "metadata", `${group}_endpoint_catalog.json`), deduped);
  return deduped;
}

class DartClient {
  constructor(apiKey, outputDir) {
    this.apiKey = apiKey;
    this.cacheDir = path.join(outputDir, "raw", "api_cache");
    this.stats = { network_requests: 0, cache_hits: 0, success: 0, no_data: 0, errors: 0 };
  }

  cachePath(endpoint, params) {
    const key = createHash("sha256").update(JSON.stringify([endpoint, Object.entries(params).sort()])).digest("hex").slice(0, 24);
    return path.join(this.cacheDir, endpointStem(endpoint), `${key}.json`);
  }

  async json(endpoint, params = {}) {
    const cacheFile = this.cachePath(endpoint, params);
    if (existsSync(cacheFile)) {
      this.stats.cache_hits += 1;
      return JSON.parse(await readFile(cacheFile, "utf8")).response;
    }
    const url = new URL(`${DART_BASE}/${endpoint}`);
    url.searchParams.set("crtfc_key", this.apiKey);
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
    const bytes = await fetchBuffer(url, {}, 5);
    this.stats.network_requests += 1;
    let response;
    try { response = JSON.parse(bytes.toString("utf8")); }
    catch { throw new Error(`${endpoint}: JSON이 아닌 응답을 받았습니다.`); }
    await writeJson(cacheFile, { fetched_at: new Date().toISOString(), endpoint, params, response });
    if (response.status === "000") this.stats.success += 1;
    else if (response.status === "013") this.stats.no_data += 1;
    else this.stats.errors += 1;
    if (FATAL_DART_STATUSES.has(response.status)) throw new Error(`${endpoint}: OpenDART 오류 ${response.status} (${response.message ?? ""})`);
    return response;
  }

  async zip(endpoint, params, destination) {
    if (existsSync(destination)) return { cached: true };
    const url = new URL(`${DART_BASE}/${endpoint}`);
    url.searchParams.set("crtfc_key", this.apiKey);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
    const bytes = await fetchBuffer(url, {}, 5);
    this.stats.network_requests += 1;
    if (bytes.subarray(0, 2).toString("ascii") !== "PK") {
      const text = bytes.toString("utf8");
      const status = text.match(/<status>([^<]+)<\/status>/)?.[1] ?? "unknown";
      if (status === "013") { this.stats.no_data += 1; return { no_data: true }; }
      if (FATAL_DART_STATUSES.has(status)) throw new Error(`${endpoint}: OpenDART ZIP 오류 ${status}`);
      this.stats.errors += 1;
      return { error: status };
    }
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, bytes);
    this.stats.success += 1;
    return { saved: true, bytes: bytes.length };
  }
}

function enrichRows(response, context) {
  const rows = Array.isArray(response?.list) ? response.list : [];
  return rows.map((row) => ({ ...context, ...row }));
}

async function runPool(tasks, concurrency, onProgress) {
  let index = 0;
  let completed = 0;
  const worker = async () => {
    while (index < tasks.length) {
      const taskIndex = index++;
      await tasks[taskIndex]();
      completed += 1;
      if (completed % 100 === 0 || completed === tasks.length) onProgress?.(completed, tasks.length);
      await new Promise((resolve) => setTimeout(resolve, 75));
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, tasks.length) }, worker));
}

async function collectDisclosures(client, company, begin, end) {
  const rows = [];
  let page = 1;
  while (true) {
    const response = await client.json("list.json", {
      corp_code: company.corp_code, bgn_de: begin, end_de: end, page_no: page, page_count: 100,
    });
    rows.push(...enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank }));
    const totalPages = Number(response.total_page ?? 1);
    if (page >= totalPages || response.status !== "000") break;
    page += 1;
  }
  return rows;
}

function findPeriodicReports(disclosures, period) {
  return disclosures.filter((row) => row.report_nm?.includes(period.label) && row.report_nm?.includes(period.marker) && !row.report_nm?.includes("첨부"));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    console.log("Usage: node scripts/collect-opendart-kospi-top100.mjs [--universe-only] [--as-of YYYY-MM-DD] [--concurrency 1..10] [--output DIR]");
    return;
  }
  const stamp = options.asOf.replaceAll("-", "");
  const outputDir = path.resolve(options.output ?? path.join("output", `opendart-kospi-top100-${stamp}`));
  await mkdir(outputDir, { recursive: true });
  console.log(`[1/7] KOSPI 보통주 시가총액 상위 100 구성: ${options.asOf}`);
  const universe = await buildUniverse(outputDir, options.asOf);
  const apiKey = await loadApiKey();
  if (!/^[A-Za-z0-9]{40}$/.test(apiKey)) throw new Error("DART_API_KEY 형식이 올바르지 않습니다.");
  const client = new DartClient(apiKey, outputDir);
  console.log("[2/7] OpenDART 고유번호 및 공식 API 카탈로그 수집");
  const corpZip = await fetchBuffer(`${DART_BASE}/corpCode.xml?crtfc_key=${encodeURIComponent(apiKey)}`);
  if (corpZip.subarray(0, 2).toString("ascii") !== "PK") throw new Error("OpenDART 고유번호 파일 요청에 실패했습니다.");
  const corpXml = extractFirstZipEntry(corpZip).toString("utf8");
  await mkdir(path.join(outputDir, "raw"), { recursive: true });
  await writeFile(path.join(outputDir, "raw", "corpCode.zip"), corpZip);
  await writeFile(path.join(outputDir, "raw", "corpCode.xml"), corpXml, "utf8");
  attachCorpCodes(universe, parseCorpCodeXml(corpXml));
  await writeRows(path.join(outputDir, "normalized"), "universe", universe);
  if (options.universeOnly) {
    console.log(`완료: ${path.join(outputDir, "normalized", "universe.jsonl")}`);
    return;
  }
  const [periodicCatalog, ownershipCatalog, eventCatalog, securitiesCatalog] = await Promise.all(
    ["DS002", "DS004", "DS005", "DS006"].map((group) => discoverEndpoints(group, outputDir)),
  );
  console.log(`공식 API 발견: 정기 ${periodicCatalog.length}, 지분 ${ownershipCatalog.length}, 주요사항 ${eventCatalog.length}, 증권 ${securitiesCatalog.length}`);

  const datasets = new Map();
  const push = (name, rows) => {
    if (!datasets.has(name)) datasets.set(name, []);
    datasets.get(name).push(...rows);
  };
  const disclosureByCode = new Map();
  const begin = "20250101";
  const end = options.asOf.replaceAll("-", "");
  console.log("[3/7] 기업개황과 공시목록 수집");
  await runPool(universe.flatMap((company) => [
    async () => {
      const response = await client.json("company.json", { corp_code: company.corp_code });
      if (response.status === "000") push("companies", [{ stock_code: company.stock_code, universe_rank: company.universe_rank, ...response }]);
    },
    async () => {
      const rows = await collectDisclosures(client, company, begin, end);
      disclosureByCode.set(company.stock_code, rows);
      push("disclosures", rows);
    },
  ]), options.concurrency, (done, total) => console.log(`  기본정보 ${done}/${total}`));

  console.log("[4/7] 정기보고서 주요정보·재무제표·재무지표 수집");
  const periodicTasks = [];
  for (const company of universe) {
    for (const item of periodicCatalog.filter((item) => item.endpoint.endsWith(".json"))) {
      periodicTasks.push(async () => {
        const response = await client.json(item.endpoint, { corp_code: company.corp_code, bsns_year: "2025", reprt_code: "11011" });
        push(`periodic_${endpointStem(item.endpoint)}_2025_annual`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, period_key: "2025_annual", source_endpoint: item.endpoint }));
      });
      if (INTERIM_PERIODIC.has(endpointStem(item.endpoint))) {
        periodicTasks.push(async () => {
          const response = await client.json(item.endpoint, { corp_code: company.corp_code, bsns_year: "2026", reprt_code: "11012" });
          push(`periodic_${endpointStem(item.endpoint)}_2026_half`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, period_key: "2026_half", source_endpoint: item.endpoint }));
        });
      }
    }
    for (const period of REPORT_PERIODS) {
      periodicTasks.push(async () => {
        let response = await client.json("fnlttSinglAcntAll.json", { corp_code: company.corp_code, bsns_year: period.year, reprt_code: period.code, fs_div: "CFS" });
        let fsDiv = "CFS";
        if (response.status === "013") {
          response = await client.json("fnlttSinglAcntAll.json", { corp_code: company.corp_code, bsns_year: period.year, reprt_code: period.code, fs_div: "OFS" });
          fsDiv = "OFS";
        }
        push(`financial_statements_${period.key}`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, period_key: period.key, requested_fs_div: fsDiv, source_endpoint: "fnlttSinglAcntAll.json" }));
      });
      for (const idxClCode of RATIO_CATEGORIES) {
        periodicTasks.push(async () => {
          const response = await client.json("fnlttSinglIndx.json", { corp_code: company.corp_code, bsns_year: period.year, reprt_code: period.code, idx_cl_code: idxClCode });
          push(`financial_indicators_${period.key}`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, period_key: period.key, idx_cl_code: idxClCode, source_endpoint: "fnlttSinglIndx.json" }));
        });
      }
    }
  }
  await runPool(periodicTasks, options.concurrency, (done, total) => console.log(`  정기·재무 ${done}/${total}`));

  console.log("[5/7] 지분·주요사항·증권신고서 데이터 수집");
  const eventTasks = [];
  for (const company of universe) {
    for (const item of ownershipCatalog.filter((item) => item.endpoint.endsWith(".json"))) {
      eventTasks.push(async () => {
        const response = await client.json(item.endpoint, { corp_code: company.corp_code });
        push(`ownership_${endpointStem(item.endpoint)}`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, source_endpoint: item.endpoint }));
      });
    }
    for (const [kind, catalog] of [["major_event", eventCatalog], ["securities", securitiesCatalog]]) {
      for (const item of catalog.filter((entry) => entry.endpoint.endsWith(".json"))) {
        eventTasks.push(async () => {
          const response = await client.json(item.endpoint, { corp_code: company.corp_code, bgn_de: begin, end_de: end });
          push(`${kind}_${endpointStem(item.endpoint)}`, enrichRows(response, { stock_code: company.stock_code, universe_rank: company.universe_rank, source_endpoint: item.endpoint, query_begin: begin, query_end: end }));
        });
      }
    }
  }
  await runPool(eventTasks, options.concurrency, (done, total) => console.log(`  지분·이벤트 ${done}/${total}`));

  console.log("[6/7] 공시원문 XML ZIP과 XBRL ZIP 수집");
  const documentTasks = [];
  const documentIndex = [];
  for (const company of universe) {
    const disclosures = disclosureByCode.get(company.stock_code) ?? [];
    for (const period of REPORT_PERIODS) {
      const reports = findPeriodicReports(disclosures, period);
      const report = reports[0];
      if (!report?.rcept_no) {
        documentIndex.push({ stock_code: company.stock_code, corp_name: company.corp_name, period_key: period.key, status: "report_not_found" });
        continue;
      }
      documentTasks.push(async () => {
        let docResult = { error: "not_attempted" };
        let documentReport = report;
        let documentPath = "";
        for (const candidate of reports) {
          const candidatePath = path.join(outputDir, "raw", "documents", `${company.stock_code}_${candidate.rcept_no}_${period.key}.zip`);
          const candidateResult = await client.zip("document.xml", { rcept_no: candidate.rcept_no }, candidatePath);
          docResult = candidateResult;
          documentReport = candidate;
          documentPath = candidatePath;
          if (candidateResult.saved || candidateResult.cached) break;
        }
        const xbrlPath = path.join(outputDir, "raw", "xbrl", `${company.stock_code}_${report.rcept_no}_${period.key}.zip`);
        const xbrlResult = await client.zip("fnlttXbrl.xml", { rcept_no: report.rcept_no, reprt_code: period.code }, xbrlPath);
        documentIndex.push({
          stock_code: company.stock_code, corp_name: company.corp_name, period_key: period.key,
          rcept_no: report.rcept_no, report_nm: report.report_nm, document_rcept_no: documentReport.rcept_no,
          document_fallback_used: documentReport.rcept_no !== report.rcept_no,
          document_zip: docResult.saved || docResult.cached ? path.relative(outputDir, documentPath) : "",
          xbrl_zip: xbrlResult.saved || xbrlResult.cached ? path.relative(outputDir, xbrlPath) : "",
          document_status: docResult.saved ? "saved" : docResult.cached ? "cached" : docResult.no_data ? "no_data" : "error",
          xbrl_status: xbrlResult.saved ? "saved" : xbrlResult.cached ? "cached" : xbrlResult.no_data ? "no_data" : "error",
        });
      });
    }
  }
  await runPool(documentTasks, Math.min(options.concurrency, 3), (done, total) => console.log(`  원문·XBRL ${done}/${total}`));
  push("document_index", documentIndex);

  console.log("[7/7] 정규화 CSV/JSONL과 수집 명세 저장");
  const normalizedDir = path.join(outputDir, "normalized");
  for (const [name, rows] of datasets) await writeRows(normalizedDir, name, rows);
  const coverage = [...datasets].map(([dataset, rows]) => ({
    dataset, row_count: rows.length, company_count: new Set(rows.map((row) => row.stock_code).filter(Boolean)).size,
  })).sort((a, b) => a.dataset.localeCompare(b.dataset));
  await writeRows(normalizedDir, "coverage", coverage);
  const manifest = {
    generated_at: new Date().toISOString(), as_of: options.asOf, output_directory: outputDir,
    universe_count: universe.length, universe_definition: "KOSPI 보통주 시가총액 상위 100",
    query_window: { begin: "2025-01-01", end: options.asOf },
    report_periods: REPORT_PERIODS, ratio_categories: RATIO_CATEGORIES,
    endpoint_counts: { DS002: periodicCatalog.length, DS004: ownershipCatalog.length, DS005: eventCatalog.length, DS006: securitiesCatalog.length },
    dataset_count: datasets.size, datasets: coverage, api_stats: client.stats,
    sources: {
      opendart: "https://opendart.fss.or.kr/", krx_kind: KRX_CORP_URL,
      market_cap_ranking: `${NAVER_CAP_URL}1`, api_guides: `${GUIDE_URL}/main.do`,
    },
    secret_handling: "API 키는 결과·로그·캐시에 저장하지 않음",
  };
  await writeJson(path.join(outputDir, "manifest.json"), manifest);
  console.log(`완료: ${outputDir}`);
  console.log(`데이터셋 ${datasets.size}개, 네트워크 요청 ${client.stats.network_requests}, 캐시 사용 ${client.stats.cache_hits}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`수집 실패: ${error.message}`);
    process.exitCode = 1;
  });
}
