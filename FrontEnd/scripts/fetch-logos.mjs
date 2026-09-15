/**
 * 기업 로고 수집 — 종목코드/티커 → 대표 도메인(계열사는 그룹 도메인) 순으로
 * Clearbit Logo API, Google favicon 서비스를 차례로 시도해 public/company-logos/{code}.png 로 저장한다.
 * 이미 있는 파일은 건너뛴다. 실행: npm run logos:fetch  (--force 로 덮어쓰기)
 * 저장된 목록은 src/lib/logo-codes.json 으로 다시 생성된다.
 */
import { mkdir, readdir, writeFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = path.join(root, "public", "company-logos");
const force = process.argv.includes("--force");

/** code → domain. 국내 계열사는 그룹 대표 도메인을 함께 둔다 (앞의 것부터 시도) */
const DOMAINS = {
  // 반도체
  "005930": ["samsung.com"],
  "000660": ["skhynix.com"],
  "042700": ["hanmisemi.com"],
  "000990": ["dbhitek.com", "dbgroup.co.kr"],
  "009150": ["samsungsem.com", "samsung.com"],
  "011070": ["lginnotek.com", "lg.com"],
  "402340": ["sksquare.com", "sk.com"],
  "403870": ["hpsp.co.kr"],
  "058470": ["leeno.com"],
  "039030": ["eotechnics.com"],
  "036930": ["jseng.co.kr"],
  "240810": ["wonik-ips.com", "wonik.com"],
  "089030": ["techwing.co.kr"],
  "095340": ["isc21.kr"],
  NVDA: ["nvidia.com"],
  AMD: ["amd.com"],
  INTC: ["intel.com"],
  QCOM: ["qualcomm.com"],
  AVGO: ["broadcom.com"],
  MU: ["micron.com"],
  TXN: ["ti.com"],
  AMAT: ["appliedmaterials.com"],
  LRCX: ["lamresearch.com"],
  KLAC: ["kla.com"],
  ASML: ["asml.com"],
  MRVL: ["marvell.com"],
  ARM: ["arm.com"],
  MCHP: ["microchip.com"],
  ON: ["onsemi.com"],
  ADI: ["analog.com"],
  NXPI: ["nxp.com"],
  GFS: ["gf.com"],
  TSM: ["tsmc.com"],
  // 2차전지
  "373220": ["lgensol.com", "lg.com"],
  "006400": ["samsungsdi.com", "samsung.com"],
  "247540": ["ecoprobm.co.kr", "ecopro.co.kr"],
  "086520": ["ecopro.co.kr"],
  "003670": ["poscofuturem.com", "posco.com"],
  "066970": ["landf.co.kr"],
  "011790": ["skc.kr", "sk.com"],
  "020150": ["lotteenergymaterials.com", "lotte.co.kr"],
  "005070": ["cosmoamt.com"],
  "300750": ["catl.com"],
  "6752": ["panasonic.com"],
  ENVX: ["enovix.com"],
  QS: ["quantumscape.com"],
  // 자동차
  "005380": ["hyundai.com"],
  "000270": ["kia.com"],
  "012330": ["mobis.co.kr", "hyundai.com"],
  "018880": ["hanonsystems.com"],
  "204320": ["hlmando.com", "hlholdings.com"],
  "011210": ["hyundai-wia.com", "hyundai.com"],
  "161390": ["hankooktire.com"],
  "073240": ["kumhotire.com"],
  TSLA: ["tesla.com"],
  RIVN: ["rivian.com"],
  LCID: ["lucidmotors.com"],
  LI: ["lixiang.com"],
  GM: ["gm.com"],
  F: ["ford.com"],
  // 인터넷·플랫폼
  "035420": ["navercorp.com", "naver.com"],
  "035720": ["kakaocorp.com", "kakao.com"],
  "259960": ["krafton.com"],
  "036570": ["ncsoft.com"],
  "251270": ["netmarble.com"],
  "293490": ["kakaogames.com", "kakao.com"],
  "263750": ["pearlabyss.com"],
  AMZN: ["amazon.com"],
  GOOGL: ["google.com"],
  META: ["meta.com"],
  BKNG: ["bookingholdings.com", "booking.com"],
  ABNB: ["airbnb.com"],
  DASH: ["doordash.com"],
  MELI: ["mercadolibre.com"],
  PDD: ["pddholdings.com", "temu.com"],
  SHOP: ["shopify.com"],
  EBAY: ["ebay.com"],
  // 소프트웨어·AI
  MSFT: ["microsoft.com"],
  PLTR: ["palantir.com"],
  ADBE: ["adobe.com"],
  INTU: ["intuit.com"],
  SNPS: ["synopsys.com"],
  CDNS: ["cadence.com"],
  ADSK: ["autodesk.com"],
  WDAY: ["workday.com"],
  DDOG: ["datadoghq.com"],
  CRWD: ["crowdstrike.com"],
  PANW: ["paloaltonetworks.com"],
  FTNT: ["fortinet.com"],
  ZS: ["zscaler.com"],
  APP: ["applovin.com"],
  TEAM: ["atlassian.com"],
  "018260": ["samsungsds.com", "samsung.com"],
  "307950": ["hyundai-autoever.com", "hyundai.com"],
  "064400": ["lgcns.com", "lg.com"],
  "012510": ["douzone.com"],
  // 바이오
  "207940": ["samsungbiologics.com", "samsung.com"],
  "068270": ["celltrion.com"],
  "000100": ["yuhan.co.kr"],
  "196170": ["alteogen.com"],
  "326030": ["skbp.com", "sk.com"],
  "128940": ["hanmipharm.com"],
  "028300": ["hlb-life.com", "hlbpharma.com"],
  AMGN: ["amgen.com"],
  GILD: ["gilead.com"],
  REGN: ["regeneron.com"],
  VRTX: ["vrtx.com"],
  MRNA: ["modernatx.com"],
  ISRG: ["intuitive.com"],
  AZN: ["astrazeneca.com"],
  DXCM: ["dexcom.com"],
  // (구)에너지·중공업 → 전력·에너지설비 · 조선·해운·운송 · 방산·항공우주 · 정유·화학·에너지
  "034020": ["doosanenerbility.com", "doosan.com"],
  "267260": ["hd-hyundaielectric.com", "hd.com"],
  "329180": ["hd-hhi.co.kr", "hd.com"],
  "042660": ["hanwhaocean.com", "hanwha.com"],
  "010140": ["samsungshi.com", "samsung.com"],
  "010120": ["ls-electric.com", "lsholdings.com"],
  "298040": ["hyosungheavy.com", "hyosung.com"],
  "012450": ["hanwhaaerospace.com", "hanwha.com"],
  "064350": ["hyundai-rotem.co.kr", "hyundai.com"],
  "079550": ["lignex1.com"],
  "015760": ["kepco.co.kr"],
  "009830": ["hanwhasolutions.com", "hanwha.com"],
  "096770": ["skinnovation.com", "sk.com"],
  "010950": ["s-oil.com"],
  CEG: ["constellationenergy.com"],
  BKR: ["bakerhughes.com"],
  FSLR: ["firstsolar.com"],
  ENPH: ["enphase.com"],
  // (구)금융·지주 → 금융 · 지주회사
  "105560": ["kbfg.com", "kbstar.com"],
  "055550": ["shinhangroup.com", "shinhan.com"],
  "086790": ["hanafn.com", "hanabank.com"],
  "316140": ["woorifg.com", "wooribank.com"],
  "138040": ["meritzfg.com", "meritz.co.kr"],
  "032830": ["samsunglife.com", "samsung.com"],
  "000810": ["samsungfire.com", "samsung.com"],
  "006800": ["miraeasset.com"],
  "323410": ["kakaobank.com", "kakao.com"],
  "377300": ["kakaopay.com", "kakao.com"],
  "028260": ["samsungcnt.com", "samsung.com"],
  "034730": ["sk-inc.com", "sk.com"],
  "003550": ["lgcorp.com", "lg.com"],
  "000880": ["hanwha.com", "hanwha.co.kr"],
  "000150": ["doosan.com"],
  "267250": ["hd.com", "hd-hyundai.com"],
  "004990": ["lotte.co.kr", "lotte.com"],
  "005490": ["posco-inc.com", "posco.com"],
  PYPL: ["paypal.com"],
  COIN: ["coinbase.com"],
  CME: ["cmegroup.com"],
  // 소비재·유통
  AAPL: ["apple.com"],
  COST: ["costco.com"],
  PEP: ["pepsico.com"],
  SBUX: ["starbucks.com"],
  MDLZ: ["mondelezinternational.com"],
  LULU: ["lululemon.com"],
  "090430": ["amorepacific.com", "apgroup.com"],
  "051900": ["lghnh.com", "lg.com"],
  "097950": ["cj.co.kr", "cj.net"],
  "271560": ["orionworld.com"],
  "003230": ["samyangfoods.com"],
  "004170": ["shinsegae.com"],
  "139480": ["emart.com", "shinsegae.com"],
  "023530": ["lotteshopping.com", "lotte.co.kr"],
  "383220": ["fnf.co.kr"],
  // (구)통신·미디어 → 통신 · 미디어·게임·엔터
  "017670": ["sktelecom.com", "sk.com"],
  "030200": ["kt.com"],
  "032640": ["lguplus.com", "lg.com"],
  "352820": ["hybecorp.com"],
  "035900": ["jype.com"],
  "041510": ["smentertainment.com"],
  NFLX: ["netflix.com"],
  CMCSA: ["comcast.com"],
  TMUS: ["t-mobile.com"],
  EA: ["ea.com"],
  TTWO: ["take2games.com"],
  // (구)철강·화학·소재 → 철강·비철·소재 · 정유·화학·에너지
  "004020": ["hyundai-steel.com", "hyundai.com"],
  "010130": ["koreazinc.co.kr"],
  "051910": ["lgchem.com", "lg.com"],
  "011170": ["lottechem.com", "lotte.co.kr"],
  "011780": ["kkpc.com"],
  "014680": ["hansolchemical.com", "hansol.com"],
  "005290": ["dongjin.com"],
  "357780": ["soulbrain.co.kr"],
  "298020": ["hyosungtnc.com", "hyosung.com"],
  LIN: ["linde.com"],
  ALB: ["albemarle.com"],
  STLD: ["steeldynamics.com"],
  // 산업군 재편으로 추가된 기업
  "066570": ["lge.co.kr", "lg.com"],
  "000720": ["hdec.kr", "hyundai.com"],
  "241560": ["doosanbobcat.com", "bobcat.com"],
  "042670": ["hd-infracore.com", "hd.com"],
  "028050": ["samsungena.com", "samsung.com"],
  "047810": ["koreaaero.com"],
  "011200": ["hmm21.com"],
  "003490": ["koreanair.com"],
};

const UA = "Mozilla/5.0 (compatible; cosmos-logo-fetch/1.0)";

async function tryFetch(url, minBytes) {
  try {
    const res = await fetch(url, { headers: { "User-Agent": UA }, redirect: "follow", signal: AbortSignal.timeout(12000) });
    if (!res.ok) return null;
    const type = res.headers.get("content-type") ?? "";
    if (!type.startsWith("image/")) return null;
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length < minBytes) return null;
    return { buf, type };
  } catch {
    return null;
  }
}

async function fetchLogo(domain) {
  // 1) Clearbit — 정사각형 고해상도 로고 (서비스 중단 시 건너뜀)
  const a = await tryFetch(`https://logo.clearbit.com/${domain}?size=256`, 1500);
  if (a) return { ...a, source: "clearbit", ext: "png" };
  // 2) 사이트의 apple-touch-icon (보통 180px 정방형)
  for (const host of [domain, `www.${domain}`]) {
    const b = await tryFetch(`https://${host}/apple-touch-icon.png`, 1500);
    if (b) return { ...b, source: "touch-icon", ext: "png" };
  }
  // 3) Google favicon 256px (16px 기본 아이콘은 크기로 걸러낸다)
  const c = await tryFetch(`https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://${domain}&size=256`, 2500);
  if (c) return { ...c, source: "gstatic", ext: "png" };
  // 4) DuckDuckGo 아이콘 (ico, 다중 크기)
  const d = await tryFetch(`https://icons.duckduckgo.com/ip3/${domain}.ico`, 1500);
  if (d) return { ...d, source: "ddg", ext: d.type.includes("png") ? "png" : "ico" };
  // 5) 사이트 루트의 favicon.ico / precomposed 터치 아이콘
  for (const host of [`www.${domain}`, domain]) {
    const e = await tryFetch(`https://${host}/favicon.ico`, 1500);
    if (e) return { ...e, source: "favicon", ext: e.type.includes("png") ? "png" : e.type.includes("svg") ? "svg" : "ico" };
    const f = await tryFetch(`https://${host}/apple-touch-icon-precomposed.png`, 1500);
    if (f) return { ...f, source: "touch-icon-pre", ext: f.type.includes("jpeg") ? "jpg" : "png" };
  }
  // 6) logo.dev (공개 문서용 publishable key, 프로토타입 한정 — 서비스 시 자체 키·표기 필요)
  const g = await tryFetch(`https://img.logo.dev/${domain}?token=pk_X-1ZO13GSgeOoUrIuJ6GMQ&size=256&format=png`, 1500);
  if (g) return { ...g, source: "logo.dev", ext: g.type.includes("jpeg") ? "jpg" : "png" };
  return null;
}

async function exists(p) {
  try {
    return (await stat(p)).size > 0;
  } catch {
    return false;
  }
}

await mkdir(outDir, { recursive: true });
const report = { kept: [], saved: [], failed: [] };
const entries = Object.entries(DOMAINS);
let i = 0;
const worker = async () => {
  while (i < entries.length) {
    const [code, domains] = entries[i++];
    const existing = (await readdir(outDir)).find((f) => f.replace(/\.[a-z]+$/, "") === code);
    if (!force && existing) {
      report.kept.push(code);
      continue;
    }
    let done = false;
    for (const d of domains) {
      const got = await fetchLogo(d);
      if (got) {
        await writeFile(path.join(outDir, `${code}.${got.ext}`), got.buf);
        report.saved.push(`${code} ← ${d} (${got.source}, ${Math.round(got.buf.length / 1024)}KB)`);
        done = true;
        break;
      }
    }
    if (!done) report.failed.push(`${code} (${domains.join(", ")})`);
  }
};
await Promise.all(Array.from({ length: 6 }, worker));

const files = (await readdir(outDir)).filter((f) => /\.(png|ico|jpg|svg)$/.test(f)).sort();
await writeFile(path.join(root, "src", "lib", "logo-codes.json"), JSON.stringify(files, null, 2) + "\n");

console.log(`kept ${report.kept.length}, saved ${report.saved.length}, failed ${report.failed.length}`);
report.saved.forEach((s) => console.log("  +", s));
report.failed.forEach((s) => console.log("  ✗", s));
console.log(`logo-codes.json: ${files.length} codes`);
