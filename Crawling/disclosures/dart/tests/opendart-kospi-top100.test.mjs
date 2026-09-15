import test from "node:test";
import assert from "node:assert/strict";
import { deflateRawSync } from "node:zlib";
import {
  extractFirstZipEntry, isCommonShareName, parseArgs, parseCorpCodeXml,
  parseKrxRegistry, parseNaverMarketCapPage, rowsToCsv, attachCorpCodes,
} from "../scripts/collect-opendart-kospi-top100.mjs";

test("순위 목록에 Python 수집기가 요구하는 DART 회사코드를 연결한다", () => {
  const rows = [{ stock_code: "005930", stock_name: "삼성전자", universe_rank: 1 }];
  attachCorpCodes(rows, [{ stock_code: "005930", corp_code: "00126380" }]);
  assert.equal(rows[0].corp_code, "00126380");
  assert.throws(() => attachCorpCodes(rows, []), /매핑 실패/);
});

test("CLI 옵션을 해석한다", () => {
  assert.deepEqual(parseArgs(["--as-of", "2026-09-07", "--concurrency", "3", "--universe-only"]), {
    asOf: "2026-09-07", concurrency: 3, universeOnly: true, output: null,
  });
});

test("보통주 이름 필터가 우선주와 펀드성 종목을 제외한다", () => {
  assert.equal(isCommonShareName("삼성전자"), true);
  assert.equal(isCommonShareName("삼성전자우"), false);
  assert.equal(isCommonShareName("현대차2우B"), false);
  assert.equal(isCommonShareName("KODEX 200 ETF"), false);
});

test("네이버 시가총액 표를 숫자형으로 읽는다", () => {
  const html = `<table class="type_2"><tr><td>1</td><td><a class="tltle" href="/item/main.naver?code=005930">삼성전자</a></td><td>80,000</td><td>1,000</td><td>1.25%</td><td>100</td><td>4,500,000</td><td>5,900,000</td><td>55.0</td><td>10,000,000</td><td>20.0</td><td>12.5</td></tr></table>`;
  const [row] = parseNaverMarketCapPage(html);
  assert.equal(row.stock_code, "005930");
  assert.equal(row.market_cap_100m_krw, 4_500_000);
  assert.equal(row.change_pct, 1.25);
});

test("OpenDART 고유번호 XML을 읽는다", () => {
  const xml = `<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code><modify_date>20260101</modify_date></list></result>`;
  assert.deepEqual(parseCorpCodeXml(xml)[0], { corp_code: "00126380", corp_name: "삼성전자", stock_code: "005930", modify_date: "20260101" });
});

test("KRX KIND 목록에서 유가증권시장 법인만 읽는다", () => {
  const html = `<table><tr><td>삼성전자</td><td>유가</td><td>005930</td><td>통신 장비</td><td>반도체</td><td>1975-06-11</td><td>12월</td></tr><tr><td>코스닥사</td><td>코스닥</td><td>123456</td><td>소프트웨어</td><td>앱</td><td>2020-01-01</td><td>12월</td></tr></table>`;
  assert.deepEqual(parseKrxRegistry(html).map((row) => row.stock_code), ["005930"]);
});

test("CSV 따옴표와 줄바꿈을 이스케이프한다", () => {
  const csv = rowsToCsv([{ a: "x,y", b: 'a"b', c: "line\nbreak" }]);
  assert.match(csv, /"x,y"/);
  assert.match(csv, /"a""b"/);
  assert.match(csv, /"line\nbreak"/);
});

function makeZip(name, data) {
  const filename = Buffer.from(name);
  const source = Buffer.from(data);
  const compressed = deflateRawSync(source);
  const local = Buffer.alloc(30);
  local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(8, 8);
  local.writeUInt32LE(compressed.length, 18); local.writeUInt32LE(source.length, 22); local.writeUInt16LE(filename.length, 26);
  const central = Buffer.alloc(46);
  central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 6); central.writeUInt16LE(8, 10);
  central.writeUInt32LE(compressed.length, 20); central.writeUInt32LE(source.length, 24); central.writeUInt16LE(filename.length, 28);
  const centralOffset = local.length + filename.length + compressed.length;
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(1, 8); end.writeUInt16LE(1, 10);
  end.writeUInt32LE(central.length + filename.length, 12); end.writeUInt32LE(centralOffset, 16);
  return Buffer.concat([local, filename, compressed, central, filename, end]);
}

test("ZIP 첫 파일을 압축 해제한다", () => {
  assert.equal(extractFirstZipEntry(makeZip("CORPCODE.xml", "<result/>" )).toString(), "<result/>");
});
