/**
 * 기업 로고 수집 — 종목코드/티커 → 대표 도메인(계열사는 그룹 도메인) 순으로 공개 아이콘 출처를 훑어
 * public/company-logos/{code}.{ext} 로 저장한다. 후보 중 긴 변 128px 이상이 나오면 그것을 쓰고,
 * 없으면 가장 큰 것을 쓴다 (가이드 권장 조건: 정사각형·투명 배경·최소 128×128).
 * 128px 보다 큰 그림은 받지 않는다 — 로고는 three/logoAtlas 의 128px 셀과 패널 아바타(≤56px)에만 쓰여서
 * 그보다 큰 픽셀은 첫 화면 전송량만 늘린다 (2026-09-18: 256px 로 받아 둔 로고 3.8MB → 1.6MB).
 * 이미 있는 파일은 건너뛴다 — 특정 기업만 다시 받으려면 그 파일을 지우고 실행한다.
 * 실행: npm run logos:fetch  (--force 로 전부 덮어쓰기)
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
  "066570": ["lg.com", "lge.co.kr"], // LG전자
  "000720": ["hdec.kr", "hyundai.com"],
  "241560": ["doosanbobcat.com", "bobcat.com", "doosan.com"], // 두산밥캣
  "042670": ["hd-infracore.com", "hd.com"],
  "028050": ["samsungena.com", "samsungengineering.com", "samsung.com"], // 삼성E&A
  "047810": ["koreaaero.com", "kai.co.kr"], // 한국항공우주
  "011200": ["hmm21.com", "hmm.co.kr"], // HMM
  "003490": ["koreanair.com", "koreanair.co.kr"], // 대한항공
  // KOSPI — 로고 가이드(2026-09-16) 누락 34종목
  "009540": ["ksoe.co.kr", "hd.com"], // HD한국조선해양
  "033780": ["ktng.com"], // KT&G
  "024110": ["ibk.co.kr", "kiup.co.kr"], // 기업은행
  "278470": ["aprbeauty.com", "medicube.co.kr"], // 에이피알
  "272210": ["hanwhasystems.com", "hanwha.com"], // 한화시스템
  "005830": ["idbins.com", "dbgroup.co.kr"], // DB손해보험
  "078930": ["gs.co.kr", "gscorp.co.kr"], // GS
  "071050": ["koreaholdings.com", "truefriend.com"], // 한국금융지주
  "047050": ["poscointl.com", "posco.com"], // 포스코인터내셔널
  "005940": ["nhqv.com", "nonghyup.com"], // NH투자증권
  "180640": ["hanjinkal.co.kr", "hanjin.com"], // 한진칼
  "443060": ["hd-hyundaims.com", "hyundai-globalservice.com", "hd.com"], // HD현대마린솔루션
  "006260": ["lsholdings.com", "ls.co.kr"], // LS
  "0126Z0": ["samsungbioepis.com", "samsung.com"], // 삼성에피스홀딩스
  "007660": ["isupetasys.com", "isu.co.kr"], // 이수페타시스
  "016360": ["samsungpop.com", "samsung.com"], // 삼성증권
  "021240": ["coway.com", "coway.co.kr"], // 코웨이
  "039490": ["kiwoom.com"], // 키움증권
  "047040": ["daewooenc.com"], // 대우건설
  "267270": ["hd-hyundaice.com", "hyundai-ce.com", "hd.com"], // HD현대건설기계
  "000500": ["gaoncable.com"], // 가온전선
  "062040": ["sanilelectric.com", "sanil.co.kr"], // 산일전기
  "175330": ["jbfg.com"], // JB금융지주
  "029780": ["samsungcard.com", "samsung.com"], // 삼성카드
  "001440": ["taihan.com"], // 대한전선
  "088350": ["hanwhalife.com", "hanwha.com"], // 한화생명
  "353200": ["daeduck.com"], // 대덕전자
  "088980": ["mkif.com", "macquarie.com"], // 맥쿼리인프라
  // NASDAQ — 로고 가이드(2026-09-16) 누락 43종목
  ALNY: ["alnylam.com"], // Alnylam
  GOOG: ["google.com", "abc.xyz"], // Alphabet C
  AEP: ["aep.com"], // American Electric Power
  ALAB: ["asteralabs.com"], // Astera Labs
  ADP: ["adp.com"], // Automatic Data Processing
  AXON: ["axon.com"], // Axon
  CTAS: ["cintas.com"], // Cintas
  CSCO: ["cisco.com"], // Cisco
  CCEP: ["cocacolaep.com"], // Coca-Cola Europacific
  CPRT: ["copart.com"], // Copart
  CRWV: ["coreweave.com"], // CoreWeave
  CSX: ["csx.com"], // CSX
  FANG: ["diamondbackenergy.com"], // Diamondback Energy
  EXC: ["exeloncorp.com"], // Exelon
  FAST: ["fastenal.com"], // Fastenal
  FER: ["ferrovial.com"], // Ferrovial
  GEHC: ["gehealthcare.com"], // GE HealthCare
  HONA: ["honeywell.com"], // Honeywell Aerospace
  HON: ["honeywell.com"], // Honeywell
  IDXX: ["idexx.com"], // Idexx
  KDP: ["keurigdrpepper.com"], // Keurig Dr Pepper
  KHC: ["kraftheinzcompany.com"], // Kraft Heinz
  LITE: ["lumentum.com"], // Lumentum
  MAR: ["marriott.com"], // Marriott
  MSTR: ["strategy.com", "microstrategy.com"], // Strategy (MicroStrategy)
  MPWR: ["monolithicpower.com"], // Monolithic Power
  MNST: ["monsterbevcorp.com", "monsterenergy.com"], // Monster Beverage
  NBIS: ["nebius.com"], // Nebius
  ORLY: ["oreillyauto.com"], // O'Reilly
  ODFL: ["odfl.com"], // Old Dominion
  PCAR: ["paccar.com"], // Paccar
  PAYX: ["paychex.com"], // Paychex
  RKLB: ["rocketlabusa.com"], // Rocket Lab
  ROP: ["ropertech.com"], // Roper
  ROST: ["rossstores.com"], // Ross Stores
  SNDK: ["sandisk.com"], // Sandisk
  STX: ["seagate.com"], // Seagate
  SPCX: ["spacex.com"], // SpaceX
  TER: ["teradyne.com"], // Teradyne
  TRI: ["thomsonreuters.com"], // Thomson Reuters
  WMT: ["walmart.com"], // Walmart
  WBD: ["wbd.com"], // Warner Bros. Discovery
  WDC: ["westerndigital.com"], // Western Digital
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

/** 확장자는 실제 content-type 으로 정한다 (favicon 이 png 인 사이트, apple-touch-icon 이 jpeg 인 사이트가 섞여 있다) */
function extOf(type) {
  if (type.includes("svg")) return "svg";
  if (type.includes("jpeg") || type.includes("jpg")) return "jpg";
  if (type.includes("webp")) return "webp";
  if (type.includes("icon")) return "ico";
  return "png";
}

/**
 * 이미지의 긴 변(px). 가이드의 권장 조건이 최소 128×128 이라, 16~48px favicon 을 로고로 잡아 두는 것을 막는 데 쓴다.
 * 헤더만 읽어 판단하며, 못 읽는 형식은 0 을 돌려 후보 비교에서 뒤로 밀린다 (SVG 는 해상도 제한이 없으므로 최댓값).
 */
function maxDim(buf) {
  if (buf.length > 24 && buf.readUInt32BE(0) === 0x89504e47) return Math.max(buf.readUInt32BE(16), buf.readUInt32BE(20)); // PNG
  if (buf.length > 6 && buf.readUInt32LE(0) === 0x00010000) {
    // ICO — 디렉터리의 크기 바이트(0 은 256)
    const n = buf.readUInt16LE(4);
    let m = 0;
    for (let i = 0; i < n && 6 + 16 * i + 1 < buf.length; i++) m = Math.max(m, buf[6 + 16 * i] || 256, buf[7 + 16 * i] || 256);
    return m;
  }
  if (buf.length > 4 && buf[0] === 0xff && buf[1] === 0xd8) {
    // JPEG — SOF 세그먼트
    for (let i = 2; i + 9 < buf.length; ) {
      if (buf[i] !== 0xff) { i++; continue; }
      const marker = buf[i + 1];
      if (marker >= 0xc0 && marker <= 0xc2) return Math.max(buf.readUInt16BE(i + 5), buf.readUInt16BE(i + 7));
      i += 2 + buf.readUInt16BE(i + 2);
    }
    return 0;
  }
  if (buf.length > 30 && buf.toString("ascii", 0, 4) === "RIFF" && buf.toString("ascii", 8, 12) === "WEBP") {
    const fmt = buf.toString("ascii", 12, 16);
    if (fmt === "VP8X") return Math.max(1 + buf.readUIntLE(24, 3), 1 + buf.readUIntLE(27, 3));
    if (fmt === "VP8 ") return Math.max(buf.readUInt16LE(26) & 0x3fff, buf.readUInt16LE(28) & 0x3fff);
    return 0;
  }
  if (buf.toString("ascii", 0, 200).includes("<svg")) return Number.MAX_SAFE_INTEGER;
  return 0;
}

const MIN_PX = 128;

/**
 * 한 도메인의 후보를 순서대로 훑되, 128px 이상이 나오면 그 자리에서 멈춘다.
 * 하나도 그 크기에 못 미치면 그중 가장 큰 것을 쓴다 — 작은 favicon 이라도 모노그램보다는 기업을 알아보기 쉽다.
 */
async function fetchLogo(domain) {
  const candidates = [
    // 1) Clearbit — 정사각형 고해상도 로고 (서비스 중단 시 건너뜀)
    ["clearbit", `https://logo.clearbit.com/${domain}?size=128`, 1500],
    // 2) 사이트의 apple-touch-icon (보통 180px 정방형)
    ["touch-icon", `https://${domain}/apple-touch-icon.png`, 1500],
    ["touch-icon", `https://www.${domain}/apple-touch-icon.png`, 1500],
    // 3) Google favicon 128px
    ["gstatic", `https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://${domain}&size=128`, 2500],
    // 4) logo.dev (공개 문서용 publishable key, 프로토타입 한정 — 서비스 시 자체 키·표기 필요)
    ["logo.dev", `https://img.logo.dev/${domain}?token=pk_X-1ZO13GSgeOoUrIuJ6GMQ&size=128&format=png`, 1500],
    // 5) DuckDuckGo 아이콘 (ico, 다중 크기)
    ["ddg", `https://icons.duckduckgo.com/ip3/${domain}.ico`, 1500],
    // 6) 사이트 루트의 favicon.ico / precomposed 터치 아이콘 — 대개 32px 이하라 마지막에 둔다
    ["favicon", `https://www.${domain}/favicon.ico`, 1500],
    ["favicon", `https://${domain}/favicon.ico`, 1500],
    ["touch-icon-pre", `https://www.${domain}/apple-touch-icon-precomposed.png`, 1500],
    ["touch-icon-pre", `https://${domain}/apple-touch-icon-precomposed.png`, 1500],
  ];
  let best = null;
  for (const [source, url, minBytes] of candidates) {
    const got = await tryFetch(url, minBytes);
    if (!got) continue;
    const dim = maxDim(got.buf);
    const hit = { ...got, source, ext: extOf(got.type), dim };
    if (dim >= MIN_PX) return hit;
    if (!best || dim > best.dim) best = hit;
  }
  return best;
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
        report.saved.push(`${code} ← ${d} (${got.source}, ${got.dim >= MIN_PX ? `${got.dim}px` : `${got.dim}px ⚠ ${MIN_PX}px 미만`}, ${Math.round(got.buf.length / 1024)}KB)`);
        done = true;
        break;
      }
    }
    if (!done) report.failed.push(`${code} (${domains.join(", ")})`);
  }
};
await Promise.all(Array.from({ length: 6 }, worker));

const files = (await readdir(outDir)).filter((f) => /\.(png|ico|jpg|svg|webp)$/.test(f)).sort();
await writeFile(path.join(root, "src", "lib", "logo-codes.json"), JSON.stringify(files, null, 2) + "\n");

console.log(`kept ${report.kept.length}, saved ${report.saved.length}, failed ${report.failed.length}`);
report.saved.forEach((s) => console.log("  +", s));
report.failed.forEach((s) => console.log("  ✗", s));
console.log(`logo-codes.json: ${files.length} codes`);
