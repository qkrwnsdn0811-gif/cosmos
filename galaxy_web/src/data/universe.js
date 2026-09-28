/**
 * universe.js — 프로토타입용 "가짜 시장" 엔진.
 *
 * 목표: 화면에 보이는 숫자가 하드코딩된 장식이 아니라,
 *       실제로 생성된 수익률 시계열에서 "계산되어" 나오게 만든다.
 *
 *   1) 팩터 모델로 일별 수익률 생성
 *        r_i(t) = beta_m * market(t) + beta_s * sector(t) + sigma_i * eps(t) + shock_i(t)
 *   2) 뉴스/거시 이벤트는 shock 으로 주입되고, 공급망을 타고 일부 전이(spill)된다.
 *   3) 그래프의 간선은 위 시계열에서 계산한 "롤링 상관계수"로 만든다.
 *
 * → 백엔드가 실제 데이터를 넣어주면 이 파일만 API 응답으로 교체하면 된다.
 *   (교체 지점은 README.md 의 "데이터 계약" 참고)
 */

/* ------------------------------------------------------------------ */
/* 결정론적 난수 (시드 고정 → 새로고침해도 같은 우주)                    */
/* ------------------------------------------------------------------ */
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function gaussFactory(rng) {
  return () => {
    let u = 0;
    let v = 0;
    while (u === 0) u = rng();
    while (v === 0) v = rng();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };
}

/* ------------------------------------------------------------------ */
/* 섹터                                                                */
/* ------------------------------------------------------------------ */
export const SECTORS = {
  semi: { id: 'semi', name: '반도체', color: '#5fb8ff' },
  battery: { id: 'battery', name: '2차전지', color: '#4fe8c0' },
  auto: { id: 'auto', name: '자동차', color: '#ffb457' },
  platform: { id: 'platform', name: '인터넷·플랫폼', color: '#b98cff' },
  bio: { id: 'bio', name: '바이오', color: '#ff7fa4' },
  energy: { id: 'energy', name: '에너지·중공업', color: '#ffe27a' },
};
export const SECTOR_LIST = Object.values(SECTORS);

/* ------------------------------------------------------------------ */
/* 기업 (MVP 범위: 국내 대형주 20종목)                                  */
/* ------------------------------------------------------------------ */
const RAW_COMPANIES = [
  // id      name              sector     cap(조) beta_m beta_s sigma  price
  ['005930', '삼성전자', 'semi', 440, 1.05, 1.0, 0.011, 78000],
  ['000660', 'SK하이닉스', 'semi', 165, 1.25, 1.3, 0.017, 226000],
  ['042700', '한미반도체', 'semi', 12, 1.15, 1.55, 0.026, 118000],
  ['403870', 'HPSP', 'semi', 3, 1.05, 1.45, 0.028, 34000],
  ['000990', 'DB하이텍', 'semi', 3, 0.85, 0.85, 0.022, 42000],
  ['373220', 'LG에너지솔루션', 'battery', 92, 1.1, 1.35, 0.019, 395000],
  ['006400', '삼성SDI', 'battery', 24, 1.05, 1.3, 0.021, 350000],
  ['247540', '에코프로비엠', 'battery', 18, 1.2, 1.6, 0.031, 180000],
  ['003670', '포스코퓨처엠', 'battery', 20, 1.1, 1.45, 0.028, 260000],
  ['005380', '현대차', 'auto', 55, 0.95, 1.15, 0.015, 245000],
  ['000270', '기아', 'auto', 44, 0.95, 1.2, 0.016, 108000],
  ['012330', '현대모비스', 'auto', 22, 0.85, 1.05, 0.015, 235000],
  ['035420', 'NAVER', 'platform', 30, 1.0, 1.2, 0.018, 195000],
  ['035720', '카카오', 'platform', 20, 1.05, 1.3, 0.021, 42000],
  ['259960', '크래프톤', 'platform', 13, 0.9, 0.95, 0.023, 265000],
  ['207940', '삼성바이오로직스', 'bio', 62, 0.7, 1.05, 0.016, 870000],
  ['068270', '셀트리온', 'bio', 38, 0.75, 1.15, 0.019, 180000],
  ['034020', '두산에너빌리티', 'energy', 13, 1.15, 1.4, 0.026, 21000],
  ['267260', 'HD현대일렉트릭', 'energy', 11, 1.1, 1.5, 0.029, 285000],
  ['010140', '삼성중공업', 'energy', 9, 1.0, 1.25, 0.024, 11000],
];

export const COMPANIES = RAW_COMPANIES.map(
  ([id, name, sector, cap, betaM, betaS, sigma, price]) => ({
    id,
    name,
    ticker: id,
    sector,
    cap,
    betaM,
    betaS,
    sigma,
    basePrice: price,
  })
);
export const COMPANY_BY_ID = Object.fromEntries(COMPANIES.map((c) => [c.id, c]));
export const idx = Object.fromEntries(COMPANIES.map((c, i) => [c.id, i]));

/* ------------------------------------------------------------------ */
/* 시간축 : 영업일 기준 500일                                           */
/* ------------------------------------------------------------------ */
export const N_DAYS = 500;
export const WINDOW = 60; // 롤링 상관계수 창 (영업일 ≈ 3개월)

const START = new Date(Date.UTC(2024, 8, 2)); // 2024-09-02 (월)
export const DATES = (() => {
  const out = [];
  const d = new Date(START);
  while (out.length < N_DAYS) {
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) out.push(new Date(d));
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
})();
export const fmtDate = (d) =>
  `${d.getUTCFullYear()}.${String(d.getUTCMonth() + 1).padStart(2, '0')}.${String(
    d.getUTCDate()
  ).padStart(2, '0')}`;
export const dateLabel = (i) => fmtDate(DATES[Math.max(0, Math.min(N_DAYS - 1, i))]);

/* ------------------------------------------------------------------ */
/* 관계 메타데이터 (사람이 큐레이션한 "왜 연결되어 있는가")               */
/* ------------------------------------------------------------------ */
export const REL_TYPES = {
  supply: { id: 'supply', name: '공급망', color: '#5fb8ff' },
  customer: { id: 'customer', name: '고객사', color: '#4fe8c0' },
  rival: { id: 'rival', name: '경쟁·대체', color: '#ff7fa4' },
  macro: { id: 'macro', name: '거시 동조', color: '#ffe27a' },
  group: { id: 'group', name: '그룹·지분', color: '#b98cff' },
  unknown: { id: 'unknown', name: '미분류', color: '#8fa3c0' },
};

const pk = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);

const RAW_RELATIONS = [
  ['000660', '042700', 'supply', 'HBM 본딩 장비(TC본더) 사실상 독점 납품 — 하이닉스 HBM 증설 사이클에 직결', 0.55],
  ['005930', '042700', 'supply', '삼성전자 HBM 라인 장비 채택 시도. 채택 여부에 따라 관계 강도가 크게 흔들림', 0.2],
  ['005930', '000660', 'rival', 'HBM·D램 점유율 경쟁이지만, 메모리 업황이라는 공통 팩터가 경쟁 효과보다 강하게 작동', 0.45],
  ['000660', '403870', 'supply', '고압 수소 어닐링 장비 공급 — 미세공정 전환 투자에 연동', 0.4],
  ['005930', '403870', 'supply', '파운드리·D램 공정 장비 공급', 0.3],
  ['005930', '000990', 'rival', '파운드리 레거시 공정 일부 중복. 규모 차이로 동조성은 약함', 0.15],
  ['005930', '006400', 'group', '삼성 그룹사 — 지배구조 이슈·그룹 리스크가 동시에 반영', 0.3],
  ['373220', '006400', 'rival', '국내 배터리 3사 중 2사. 수주 경쟁이지만 전기차 수요라는 공통 팩터가 지배적', 0.6],
  ['373220', '003670', 'supply', '양극재 장기 공급계약 — LGES 가동률이 곧 포스코퓨처엠 매출', 0.62],
  ['373220', '247540', 'supply', '하이니켈 양극재 공급', 0.55],
  ['006400', '247540', 'supply', '각형 배터리용 양극재 공급', 0.5],
  ['003670', '247540', 'rival', '양극재 국내 1·2위. 동일 원자재(리튬·니켈) 가격에 함께 노출', 0.66],
  ['005380', '000270', 'group', '현대차그룹 — 플랫폼·판매법인 공유로 실적이 거의 동행', 0.78],
  ['005380', '012330', 'group', '핵심 부품 내부 공급 + 지배구조 개편 이슈 공유', 0.7],
  ['000270', '012330', 'supply', '모듈·핵심부품 공급', 0.66],
  ['005380', '373220', 'customer', '전기차 배터리 조달 관계. 아이오닉 판매량이 배터리 수주에 반영', 0.35],
  ['000270', '006400', 'customer', 'EV9 등 전동화 라인업 배터리 조달', 0.3],
  ['035420', '035720', 'macro', '국내 플랫폼 규제·광고 경기에 동시 노출', 0.6],
  ['035420', '259960', 'macro', '성장주 밸류에이션(금리) 민감도 공유', 0.4],
  ['207940', '068270', 'macro', '바이오 섹터 금리 민감도 + FDA 이벤트 리스크 공유', 0.52],
  ['034020', '267260', 'macro', 'AI 데이터센터 전력 수요 테마 — 원전·전력기기가 같은 내러티브로 묶임', 0.6],
  ['034020', '010140', 'macro', '조선·플랜트 수주 사이클 및 그룹 리스크', 0.4],
  ['267260', '010140', 'macro', '중공업 수출 사이클 동조', 0.35],
  ['005930', '005380', 'macro', '수출 대형주 — 원/달러 환율과 관세 이슈에 함께 반응', 0.3],
  ['000660', '267260', 'unknown', 'AI 데이터센터 CAPEX라는 공통 수요원. 섹터가 달라 사전에 연결을 예상하기 어려운 구간', 0.42],
  ['005930', '035420', 'unknown', '온디바이스 AI 협업 및 반도체 수요 기대감 공유', 0.22],
  ['042700', '034020', 'unknown', 'AI 인프라 테마가 강할 때만 동조가 살아나는 조건부 관계', 0.3],
];

export const RELATION_META = {};
for (const [a, b, type, reason, strength] of RAW_RELATIONS) {
  RELATION_META[pk(a, b)] = { a, b, type, reason, strength };
}

/* ------------------------------------------------------------------ */
/* 이벤트 : 거시(전체) + 뉴스(개별기업)                                  */
/* ------------------------------------------------------------------ */
// [dayIndex, title, tone(1 호재 / -1 악재), magnitude, tag]
export const MACRO_EVENTS = [
  [40, '미 연준 금리 인하 사이클 진입 시사', 1, 0.9, '금리'],
  [96, '중동 분쟁 확대 — 유가 급등 / 운임 상승', -1, 1.0, '지정학'],
  [150, '미 신정부 관세 정책 발표 — 수출주 일괄 조정', -1, 1.2, '통상'],
  [214, '엔비디아 실적 서프라이즈 — AI CAPEX 상향', 1, 1.1, 'AI'],
  [286, '중국 경기부양 패키지 발표', 1, 0.8, '중국'],
  [352, '반도체 사이클 고점 논쟁 — 메모리 가격 피크아웃 우려', -1, 1.0, '업황'],
  [430, 'AI 데이터센터 전력난 이슈 부각', 1, 0.9, '전력'],
];

// [dayIndex, companyId, tone, magnitude(%), headline, 근거태그]
const RAW_NEWS = [
  [22, '000660', 1, 6.5, 'SK하이닉스, 엔비디아向 HBM3E 12단 퀄 통과', '공급계약'],
  [26, '042700', 1, 9.0, '한미반도체, HBM용 TC본더 대규모 수주 공시', '수주'],
  [58, '005930', -1, 4.2, '삼성전자 HBM 퀄 테스트 지연 보도', '기술'],
  [62, '000660', 1, 3.6, '메모리 고정거래가 4개월 연속 상승', '업황'],
  [88, '373220', 1, 5.0, 'LG에너지솔루션, 북미 완성차와 대형 수주 계약', '수주'],
  [92, '003670', 1, 6.2, '포스코퓨처엠, 양극재 장기 공급계약 확대', '공급계약'],
  [104, '247540', -1, 7.5, '에코프로비엠, 리튬 가격 급락에 재고평가손 우려', '원자재'],
  [118, '005380', 1, 4.4, '현대차 인도 법인 상장 흥행', '자본'],
  [124, '000270', 1, 3.8, '기아, 미국 판매 사상 최대', '실적'],
  [156, '005930', -1, 5.1, '관세 확대에 따른 수출 마진 축소 전망', '통상'],
  [162, '005380', -1, 6.0, '자동차 관세 25% 부과 검토 보도', '통상'],
  [188, '207940', 1, 5.5, '삼성바이오로직스, 글로벌 빅파마 대형 CMO 수주', '수주'],
  [196, '068270', -1, 4.8, '셀트리온 바이오시밀러 FDA 보완요구', '규제'],
  [218, '000660', 1, 7.2, 'HBM4 조기 양산 로드맵 공개', '기술'],
  [222, '042700', 1, 8.4, 'HBM4용 신형 본더 단독 공급 확정', '공급계약'],
  [228, '267260', 1, 6.8, 'HD현대일렉트릭, 북미 전력기기 수주잔고 급증', '수주'],
  [246, '034020', 1, 7.9, '두산에너빌리티, SMR 파운드리 계약 체결', '수주'],
  [268, '035420', 1, 4.6, 'NAVER, 소버린 AI 해외 수출 계약', '신사업'],
  [274, '035720', -1, 5.4, '카카오, 플랫폼 규제 법안 발의', '규제'],
  [312, '005930', 1, 5.8, '삼성전자, 2나노 파운드리 대형 고객 확보', '수주'],
  [318, '403870', 1, 7.0, 'HPSP, 신규 장비 공급처 다변화 성공', '수주'],
  [356, '000660', -1, 6.6, 'HBM 공급과잉 우려 리포트 — 목표주가 하향', '업황'],
  [360, '042700', -1, 8.2, '경쟁사 본더 진입으로 점유율 하락 전망', '경쟁'],
  [398, '373220', 1, 5.2, 'ESS용 LFP 라인 전환 성공 — 신규 수주', '신사업'],
  [434, '034020', 1, 8.6, '데이터센터 전력 공급 계약 — 가스터빈 수주', '수주'],
  [438, '267260', 1, 6.4, '변압기 판가 인상 지속', '업황'],
  [462, '000660', 1, 4.9, '차세대 HBM 단가 협상 우위 확인', '업황'],
  [470, '259960', 1, 6.1, '크래프톤 신작 글로벌 흥행', '실적'],
];

/* 공급망 전이 계수: 뉴스 충격이 파트너에게 얼마나 번지는가 */
const SPILL = 0.45;

/* 섹터별 기간 총수익률 목표 (AI 랠리 / 2차전지 조정 같은 내러티브) */
const SECTOR_DRIFT = {
  semi: 0.55,
  battery: -0.3,
  auto: 0.22,
  platform: 0.04,
  bio: 0.33,
  energy: 0.85,
};

/* 팩터 변동성 — 이 값들이 곧 "관계가 얼마나 강하게 보이는가"를 결정한다 */
const VOL = {
  market: 0.0082,
  sector: 0.0105,
  pair: 0.0115, // 큐레이션된 관계마다 부여되는 공통 팩터
  idio: 0.52, // 개별 sigma 배율
};

/* ------------------------------------------------------------------ */
/* 시계열 생성                                                          */
/* ------------------------------------------------------------------ */
function buildSeries() {
  const rng = mulberry32(20260825);
  const gauss = gaussFactory(rng);
  const M = COMPANIES.length;

  // 공통 팩터
  const market = new Float64Array(N_DAYS);
  const sectorF = {};
  for (const s of SECTOR_LIST) sectorF[s.id] = new Float64Array(N_DAYS);

  for (let t = 0; t < N_DAYS; t++) {
    market[t] = gauss() * VOL.market;
    for (const s of SECTOR_LIST) sectorF[s.id][t] = gauss() * VOL.sector;
  }
  // 거시 이벤트 → 시장 팩터에 충격 + 이후 3일 여진
  for (const [t, , tone, mag] of MACRO_EVENTS) {
    for (let k = 0; k < 4 && t + k < N_DAYS; k++) {
      market[t + k] += tone * mag * 0.011 * Math.pow(0.55, k);
    }
  }
  // "AI 인프라" 내러티브: 반도체와 에너지 섹터 팩터를 특정 구간에서만 커플링
  // → 시간에 따라 관계가 생겼다 사라지는 것을 보여주는 장치
  for (let t = 200; t < 262; t++) sectorF.energy[t] += sectorF.semi[t] * 0.85;
  for (let t = 420; t < N_DAYS; t++) sectorF.energy[t] += sectorF.semi[t] * 0.95;

  // 개별 충격
  const shock = Array.from({ length: M }, () => new Float64Array(N_DAYS));
  const addShock = (cid, t, v) => {
    const i = idx[cid];
    if (i === undefined) return;
    for (let k = 0; k < 3 && t + k < N_DAYS; k++) {
      shock[i][t + k] += v * Math.pow(0.45, k);
    }
  };
  for (const [t, cid, tone, mag] of RAW_NEWS) {
    addShock(cid, t, (tone * mag) / 100);
    // 공급망/고객사 관계로 전이 → 나중에 상관계수로 "발견"되게 만든다
    for (const meta of Object.values(RELATION_META)) {
      if (meta.type !== 'supply' && meta.type !== 'customer') continue;
      const other = meta.a === cid ? meta.b : meta.b === cid ? meta.a : null;
      if (!other) continue;
      addShock(other, t + 1, ((tone * mag) / 100) * SPILL * meta.strength);
    }
  }

  // 관계별 공통 팩터 (pair factor) — 큐레이션된 관계에만 부여
  const pairContrib = Array.from({ length: M }, () => new Float64Array(N_DAYS));
  for (const meta of Object.values(RELATION_META)) {
    const ia = idx[meta.a];
    const ib = idx[meta.b];
    if (ia === undefined || ib === undefined) continue;
    const amp = VOL.pair * meta.strength;
    // 경쟁 관계는 공통 팩터를 조금 약하게 (수요 팩터는 공유하되 점유율은 반대)
    const wa = amp;
    const wb = meta.type === 'rival' ? amp * 0.92 : amp;
    for (let t = 0; t < N_DAYS; t++) {
      const f = gauss();
      pairContrib[ia][t] += wa * f;
      pairContrib[ib][t] += wb * f;
    }
  }

  // 수익률
  const returns = Array.from({ length: M }, () => new Float64Array(N_DAYS));
  COMPANIES.forEach((c, i) => {
    for (let t = 0; t < N_DAYS; t++) {
      returns[i][t] =
        c.betaM * market[t] +
        c.betaS * sectorF[c.sector][t] +
        pairContrib[i][t] +
        c.sigma * VOL.idio * gauss() +
        shock[i][t];
    }
  });

  // 드리프트 보정: 평균을 목표 총수익률에 맞춰 재설정 (상관구조는 그대로 유지)
  const prices = Array.from({ length: M }, () => new Float64Array(N_DAYS));
  COMPANIES.forEach((c, i) => {
    const R = returns[i];
    let mean = 0;
    for (let t = 0; t < N_DAYS; t++) mean += R[t];
    mean /= N_DAYS;
    let varSum = 0;
    for (let t = 0; t < N_DAYS; t++) varSum += (R[t] - mean) ** 2;
    const variance = varSum / N_DAYS;

    const target = SECTOR_DRIFT[c.sector] + (rng() - 0.5) * 0.44;
    const mu = Math.log(1 + target) / N_DAYS + variance / 2;

    let p = c.basePrice;
    for (let t = 0; t < N_DAYS; t++) {
      R[t] = R[t] - mean + mu;
      p *= 1 + R[t];
      prices[i][t] = p;
    }
  });

  return { market, returns, prices };
}

export const SERIES = buildSeries();

/* ------------------------------------------------------------------ */
/* 롤링 상관계수 (모든 쌍 사전 계산)                                     */
/* ------------------------------------------------------------------ */
function buildCorrelations() {
  const M = COMPANIES.length;
  const R = SERIES.returns;

  // 누적합으로 O(1) 윈도우 통계
  const cs = Array.from({ length: M }, (_, i) => {
    const a = new Float64Array(N_DAYS + 1);
    const a2 = new Float64Array(N_DAYS + 1);
    for (let t = 0; t < N_DAYS; t++) {
      a[t + 1] = a[t] + R[i][t];
      a2[t + 1] = a2[t] + R[i][t] * R[i][t];
    }
    return { a, a2 };
  });

  const corr = new Map(); // "i-j" -> Float32Array(N_DAYS)
  for (let i = 0; i < M; i++) {
    for (let j = i + 1; j < M; j++) {
      const cross = new Float64Array(N_DAYS + 1);
      for (let t = 0; t < N_DAYS; t++) cross[t + 1] = cross[t] + R[i][t] * R[j][t];

      const out = new Float32Array(N_DAYS);
      for (let t = 0; t < WINDOW - 1; t++) out[t] = NaN;
      for (let t = WINDOW - 1; t < N_DAYS; t++) {
        const s = t - WINDOW + 1;
        const n = WINDOW;
        const sx = cs[i].a[t + 1] - cs[i].a[s];
        const sy = cs[j].a[t + 1] - cs[j].a[s];
        const sxx = cs[i].a2[t + 1] - cs[i].a2[s];
        const syy = cs[j].a2[t + 1] - cs[j].a2[s];
        const sxy = cross[t + 1] - cross[s];
        const cov = sxy / n - (sx / n) * (sy / n);
        const vx = sxx / n - (sx / n) ** 2;
        const vy = syy / n - (sy / n) ** 2;
        out[t] = vx > 0 && vy > 0 ? cov / Math.sqrt(vx * vy) : 0;
      }
      corr.set(`${i}-${j}`, out);
    }
  }
  return corr;
}

const CORR = buildCorrelations();

export function corrSeries(aId, bId) {
  const i = idx[aId];
  const j = idx[bId];
  if (i === undefined || j === undefined || i === j) return null;
  return CORR.get(i < j ? `${i}-${j}` : `${j}-${i}`);
}
export function corrAt(aId, bId, day) {
  const s = corrSeries(aId, bId);
  if (!s) return 0;
  const v = s[Math.max(WINDOW - 1, Math.min(N_DAYS - 1, day))];
  return Number.isNaN(v) ? 0 : v;
}

/* ------------------------------------------------------------------ */
/* 3D 레이아웃 : 섹터 = 성단, 기업 = 항성                                */
/* ------------------------------------------------------------------ */
export const NODE_POS = (() => {
  const rng = mulberry32(77);
  const pos = {};
  const sectorCount = SECTOR_LIST.length;
  const clusterCenter = {};
  SECTOR_LIST.forEach((s, k) => {
    const ang = (k / sectorCount) * Math.PI * 2 + 0.35;
    const rad = 46;
    clusterCenter[s.id] = [
      Math.cos(ang) * rad,
      Math.sin(k * 2.399) * 13,
      Math.sin(ang) * rad * 0.82,
    ];
  });

  const bySector = {};
  for (const c of COMPANIES) (bySector[c.sector] ||= []).push(c);

  for (const s of SECTOR_LIST) {
    const list = bySector[s.id] || [];
    const [cx, cy, cz] = clusterCenter[s.id];
    list.forEach((c, k) => {
      const n = list.length;
      const y = n === 1 ? 0 : 1 - (k / (n - 1)) * 2;
      const r = Math.sqrt(Math.max(0.0001, 1 - y * y));
      const phi = k * 2.399963;
      const spread = 13;
      pos[c.id] = [
        cx + Math.cos(phi) * r * spread + (rng() - 0.5) * 3,
        cy + y * spread * 0.62 + (rng() - 0.5) * 3,
        cz + Math.sin(phi) * r * spread + (rng() - 0.5) * 3,
      ];
    });
  }
  return pos;
})();

export const SECTOR_CENTERS = (() => {
  const acc = {};
  for (const c of COMPANIES) {
    const p = NODE_POS[c.id];
    const a = (acc[c.sector] ||= [0, 0, 0, 0]);
    a[0] += p[0];
    a[1] += p[1];
    a[2] += p[2];
    a[3] += 1;
  }
  const out = {};
  for (const k in acc) {
    const a = acc[k];
    out[k] = [a[0] / a[3], a[1] / a[3], a[2] / a[3]];
  }
  return out;
})();

export const nodeRadius = (c) => 0.85 + Math.sqrt(c.cap) * 0.19;

/* ------------------------------------------------------------------ */
/* 그래프 조회 API                                                      */
/* ------------------------------------------------------------------ */
export function buildGraph(day, { threshold = 0.35, types = null } = {}) {
  const edges = [];
  for (let i = 0; i < COMPANIES.length; i++) {
    for (let j = i + 1; j < COMPANIES.length; j++) {
      const a = COMPANIES[i];
      const b = COMPANIES[j];
      const c = corrAt(a.id, b.id, day);
      if (Math.abs(c) < threshold) continue;
      const meta = RELATION_META[pk(a.id, b.id)];
      const type = meta ? meta.type : 'unknown';
      if (types && !types.has(type)) continue;
      const crossSector = a.sector !== b.sector;
      edges.push({
        key: pk(a.id, b.id),
        a: a.id,
        b: b.id,
        corr: c,
        type,
        reason: meta ? meta.reason : null,
        crossSector,
        // 예상 밖 연결: 섹터가 다른데 상관이 높고, 큐레이션된 설명이 없거나 '미분류'
        serendipity: crossSector && Math.abs(c) >= 0.45 && (!meta || meta.type === 'unknown'),
      });
    }
  }
  edges.sort((x, y) => Math.abs(y.corr) - Math.abs(x.corr));
  return edges;
}

/** 한 기업 기준 이웃 (상세 차원에서 궤도에 배치) */
export function neighborsOf(id, day, { threshold = 0.3, limit = 8 } = {}) {
  const out = [];
  const self = COMPANY_BY_ID[id];
  if (!self) return out;
  for (const c of COMPANIES) {
    if (c.id === id) continue;
    const v = corrAt(id, c.id, day);
    if (Math.abs(v) < threshold) continue;
    const meta = RELATION_META[pk(id, c.id)];
    out.push({
      id: c.id,
      corr: v,
      type: meta ? meta.type : 'unknown',
      reason: meta ? meta.reason : null,
      crossSector: c.sector !== self.sector,
    });
  }
  out.sort((x, y) => Math.abs(y.corr) - Math.abs(x.corr));
  return out.slice(0, limit);
}

/** 뉴스 발생 후 k영업일 누적 수익률 → "근거" 숫자 */
export function newsImpact(companyId, day, k = 5) {
  const i = idx[companyId];
  if (i === undefined) return 0;
  let acc = 0;
  for (let t = day; t < Math.min(N_DAYS, day + k); t++) acc += SERIES.returns[i][t];
  return acc;
}

export const ALL_NEWS = RAW_NEWS.map(([day, companyId, tone, mag, headline, tag]) => ({
  day,
  id: `${companyId}-${day}`,
  companyId,
  tone,
  mag,
  headline,
  tag,
  impact: newsImpact(companyId, day, 5),
}));

export function newsFor(companyId, { from = 0, to = N_DAYS } = {}) {
  return ALL_NEWS.filter((n) => n.companyId === companyId && n.day >= from && n.day <= to);
}

/**
 * 관계 "근거 변경 이력" — 상관계수가 크게 튄 지점을 찾고,
 * 그 시점 부근 뉴스/거시 이벤트를 원인 후보로 붙여준다.
 * (실서비스에서는 백엔드의 change-point detection 결과가 여기 들어온다)
 */
export function relationTimeline(aId, bId, { upTo = N_DAYS - 1 } = {}) {
  const s = corrSeries(aId, bId);
  if (!s) return [];
  const events = [];
  const LOOK = 10; // 변화량을 재는 간격
  const MIN = 0.16; // 이 이상 움직여야 "관계가 바뀌었다"고 본다
  const BACK = LOOK + 14; // 원인 후보를 찾을 소급 구간

  const deltaAt = (t) => s[t] - s[t - LOOK];

  let t = WINDOW + LOOK;
  while (t <= upTo) {
    const d = deltaAt(t);
    if (Math.abs(d) < MIN) {
      t++;
      continue;
    }
    // 같은 방향으로 이어지는 구간에서 "가장 크게 움직인 순간"을 대표점으로 잡는다
    const sign = Math.sign(d);
    let peak = t;
    let peakAbs = Math.abs(d);
    let j = t + 1;
    while (j <= upTo) {
      const dj = deltaAt(j);
      if (Math.abs(dj) < MIN || Math.sign(dj) !== sign) break;
      if (Math.abs(dj) > peakAbs) {
        peakAbs = Math.abs(dj);
        peak = j;
      }
      j++;
    }

    // 원인 후보: 소급 구간에 걸친 양쪽 기업 뉴스 → 없으면 거시 이벤트
    const news =
      ALL_NEWS.filter(
        (n) =>
          (n.companyId === aId || n.companyId === bId) && n.day <= peak && n.day > peak - BACK
      ).sort((x, y) => y.mag - x.mag)[0] || null;
    const macro =
      MACRO_EVENTS.filter((m) => m[0] <= peak && m[0] > peak - BACK)
        .map((m) => ({ day: m[0], title: m[1], tone: m[2], tag: m[4] }))
        .pop() || null;

    events.push({
      day: peak,
      delta: s[peak] - s[peak - LOOK],
      from: s[peak - LOOK],
      to: s[peak],
      cause: news
        ? {
            kind: 'news',
            label: news.headline,
            company: news.companyId,
            tag: news.tag,
            day: news.day,
          }
        : macro
          ? { kind: 'macro', label: macro.title, tag: macro.tag, day: macro.day }
          : null,
    });

    t = j + 14; // 인접한 중복 변화점은 하나로 본다
  }
  return events.slice(-6).reverse();
}

/**
 * 큐레이션된 설명이 없는 연결(= "예상 밖 연결")에 붙일 데이터 기반 정황.
 *  - 상관 윈도우 안에서 둘이 같은 방향으로 크게 움직인 날이 며칠인지
 *  - 그 창 안에 걸쳐 있는 공통 거시 이벤트 / 양쪽 뉴스
 * 근거를 숫자와 사건으로 되돌려주어 "왜 연결됐는지"를 사용자가 직접 판단하게 한다.
 */
export function inferContext(aId, bId, day) {
  const from = Math.max(0, day - WINDOW + 1);
  const ia = idx[aId];
  const ib = idx[bId];
  let coMove = 0;
  let counterMove = 0;
  if (ia !== undefined && ib !== undefined) {
    for (let t = from; t <= day; t++) {
      const ra = SERIES.returns[ia][t];
      const rb = SERIES.returns[ib][t];
      if (Math.abs(ra) < 0.02 || Math.abs(rb) < 0.02) continue;
      if (ra * rb > 0) coMove++;
      else counterMove++;
    }
  }
  const macro = MACRO_EVENTS.filter((m) => m[0] >= from && m[0] <= day).map((m) => ({
    day: m[0],
    title: m[1],
    tag: m[4],
  }));
  const news = ALL_NEWS.filter(
    (n) => (n.companyId === aId || n.companyId === bId) && n.day >= from && n.day <= day
  ).sort((x, y) => y.mag - x.mag);
  return { from, coMove, counterMove, macro, news: news.slice(0, 3) };
}

/** 가격 정규화 시계열 (리본용) */
export function normalizedPrice(companyId, from, to) {
  const i = idx[companyId];
  const p = SERIES.prices[i];
  const base = p[from];
  const out = new Float32Array(to - from + 1);
  for (let t = from; t <= to; t++) out[t - from] = p[t] / base - 1;
  return out;
}
export function priceAt(companyId, day) {
  return SERIES.prices[idx[companyId]][Math.max(0, Math.min(N_DAYS - 1, day))];
}
export function changePct(companyId, day, k = 20) {
  const i = idx[companyId];
  const p = SERIES.prices[i];
  const a = p[Math.max(0, day - k)];
  const b = p[Math.max(0, Math.min(N_DAYS - 1, day))];
  return b / a - 1;
}
