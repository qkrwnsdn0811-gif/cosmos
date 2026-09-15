/**
 * 안내 문구의 단일 소스 — 첫 방문 투어 캡션, `?` 도움말 허브 본문, 조작 힌트, 카메라 프리셋 설명이 전부 여기서만 문장을 가져온다.
 * 같은 규칙을 두 곳에 다른 문장으로 적어 두면 사용자는 둘 중 하나를 틀렸다고 읽는다 (기획서 §7 P0-3).
 * 씬의 인코딩(굵기=점수, 색=영향 방향, 점선=무방향)은 three/toon.ts·RelationLines 와 같은 뜻이어야 하므로 그쪽을 바꾸면 여기도 고친다.
 */

export const PRODUCT = {
  name: "COSMOS",
  /** 첫 화면 1문장 — 인코딩 설명보다 먼저 "무엇을 보여주는 서비스인지" 를 말한다 */
  definition: "뉴스와 공시에서 뽑아낸 기업 간 관계를 3D 지도로 봅니다.",
  metaphor: "기업은 행성, 관계는 빛줄기입니다.",
};

/** 첫 방문 투어를 마쳤다는 표식 (기기별). 로그인 사용자용 서버 플래그는 백엔드 요청 목록(기획서 §11-3) */
export const TOUR_DONE_KEY = "cosmos_tour_done";

export type TourStepId = "intro" | "place" | "beam" | "hover" | "warp";
export const TOUR_ORDER: TourStepId[] = ["intro", "place", "beam", "hover", "warp"];

export interface TourStepCopy {
  id: TourStepId;
  /** 캡션 머리의 "n / 4". intro 는 번호가 없다 */
  index: number | null;
  title: string;
  body: string[];
  /** 주 버튼 — 없으면 이 단계는 사용자의 실제 조작(호버)이나 시간으로 넘어간다 */
  primary?: string;
}

export const TOUR_STEPS: Record<TourStepId, TourStepCopy> = {
  intro: {
    id: "intro",
    index: null,
    title: PRODUCT.name,
    body: [PRODUCT.definition, PRODUCT.metaphor],
  },
  place: {
    id: "place",
    index: 1,
    title: "자리에는 이유가 있습니다",
    body: ["두 행성이 가까울수록 관계가 강합니다.", "안쪽에 있을수록 연결이 많은 기업(허브)입니다."],
    primary: "다음",
  },
  beam: {
    id: "beam",
    index: 2,
    title: "선이 관계입니다",
    body: ["굵을수록 점수가 높습니다. 색은 좋은 소식(파랑) · 나쁜 소식(빨강) · 중립(보라)을 뜻합니다.", "관계의 종류(공급·투자·협력·경쟁)는 색이 아니라 선 위에 커서를 올려 확인합니다."],
    primary: "다음",
  },
  hover: {
    id: "hover",
    index: 3,
    title: "선 위에 커서를 올려보세요",
    body: ["클릭하지 않아도 왜 이어졌는지 한 줄로 나옵니다."],
    // 실제 호버가 주 진행 수단이지만, 터치·키보드만 쓰는 사용자도 12초를 기다리지 않고 넘어갈 수 있어야 한다
    primary: "다음",
  },
  warp: {
    id: "warp",
    index: 4,
    title: "기업을 한 번 누르면 뉴스와 연결 단계가, 두 번 누르면 관계망이 열립니다",
    body: ["미리보기에서 다른 기업에 커서를 올리면 두 기업 사이 경로가 위에 뜹니다.", "관계망에서 돌아올 때는 ESC 또는 왼쪽 위 ←."],
    primary: "시작하기",
  },
};

/** 콜드스타트 — 스냅샷이 희소해 단계의 표본(굵은 빔·연결 많은 기업)이 없을 때 캡션을 바꾸고 검색바를 가리킨다 */
export const TOUR_FALLBACK = {
  /** beam: 점수 60 이상 간선이 하나도 없다 */
  beamSparse: { title: "지금 화면에는 표시할 만큼 강한 관계가 없습니다", body: ["검색으로 기업을 찾아보세요."] },
  /** hover: 연결 3개 이상 기업이 없다 */
  hoverSparse: { title: "기업을 검색해 시작해 보세요", body: ["예: 삼성, hynix, 005930"] },
  /** 투어 표본 기준 */
  beamMinScore: 60,
  hoverMinDegree: 3,
};

/** 3단계에서 호버가 없을 때 — 커서 고스트를 띄우는 시각(ms)과 자동으로 넘어가는 시각(ms) */
export const TOUR_HOVER_HINT_MS = 6000;
export const TOUR_HOVER_TIMEOUT_MS = 12000;
/** intro 카드 유지 시간(ms) — 씬 인트로 비행(2.4s)이 끝나고 0.2s 뒤에 1단계로. 비행 중 원반이 자리를 잡은 다음 구멍이 뚫린다 */
export const TOUR_INTRO_MS = 2600;
/** intro 카드 머리글 — 로딩 화면(GalaxyPage stage-loading)과 같은 결 */
export const TOUR_INTRO_KICKER = "news × relationship graph";

export const TOUR_DONE_TOAST = "언제든 ? 를 눌러 다시 볼 수 있습니다";

export const TOUR_BUTTONS = { skip: "건너뛰기", next: "다음", start: "시작하기" };

/** `?` 허브·헤더 버튼의 화면 문구 — 본문(HELP_SECTIONS)과 마찬가지로 여기서만 */
export const HELP_UI = {
  button: "사용 안내",
  buttonTitle: "사용 안내 (?)",
  kicker: "사용 안내",
  title: "COSMOS 읽는 법",
  replay: "투어 다시 보기",
  replayTitle: "처음 방문 때의 4단계 안내를 다시 봅니다",
  close: "닫기",
  closeTitle: "닫기 (ESC · ?)",
  showOnScreen: "화면에서 보기",
  showOnScreenTitle: "이 절을 실제 화면 위에서 보여 줍니다",
  tocLabel: "도움말 목차",
};

/** 조작 힌트 — 투어 4단계 캡션과 도움말 6절이 같은 표를 쓴다 */
export const CONTROL_HINTS: { keys: string; does: string }[] = [
  { keys: "드래그", does: "회전" },
  { keys: "휠", does: "확대·축소" },
  { keys: "W A S D", does: "시선 이동" },
  { keys: "← → ↑ ↓", does: "회전" },
  { keys: "행성 끌기", does: "자리 옮기기 (이 화면에서만)" },
  { keys: "클릭", does: "기업 미리보기 — 뉴스·연결 단계가 옆에 뜬다" },
  { keys: "더블클릭", does: "그 기업의 관계망으로" },
  { keys: "/", does: "기업 검색" },
  { keys: "?", does: "이 안내" },
  { keys: "ESC", does: "한 겹 닫기 · 은하로 복귀" },
];

export type CameraPresetId = "orbit" | "top" | "front";
/** 카메라 프리셋 — 3D 가 돕는 순간(둘러보기)과 방해하는 순간(정밀 비교)을 시점으로 나눈다 (기획서 §5-3) */
export const CAMERA_PRESETS: { id: CameraPresetId; label: string; hint: string }[] = [
  { id: "orbit", label: "궤도", hint: "전체를 둘러보기" },
  { id: "top", label: "위에서", hint: "어느 기업이 가까운지 비교" },
  { id: "front", label: "정면에서", hint: "주가 등락 높이를 비교" },
];
export const CAMERA_UI = {
  groupLabel: "카메라 시점",
  /** 버튼 title 뒤에 붙는 설명 — 같은 프리셋을 다시 누르면 드래그로 흐트러진 시점이 되돌아온다 */
  reapply: "다시 누르면 이 시점으로 되돌립니다",
};

/* ---- 상시 "읽는 법" 바 (P0-1) · 범례 카드 (P0-10) ---- */
export const READBAR = {
  label: "읽는 법",
  /** 축약 1행 — 순서대로 자리·굵기·색·점선·글자. 씬 인코딩(RelationLines·toon)과 같은 뜻이어야 한다 */
  items: [
    { key: "place", text: "위치=관계 강도" },
    { key: "width", text: "굵기=점수" },
    { key: "color", text: "색=영향 방향" },
    { key: "dash", text: "점선=방향 없음" },
    { key: "label", text: "선 위 글자=관계 종류" },
  ],
  expand: "필터·범례 펼치기",
  collapse: "필터·범례 접기",
};
export const LEGEND_UI = {
  kicker: "관계 필터",
  typesTitle: "무엇이 이어졌나 — 종류",
  typesHint: "선 위 글자와 기업 호버 시 이웃 링·태그로 표시 (색이 아님)",
  impactTitle: "좋은 신호인가 — 영향 (선 색)",
  impact: [
    { key: "POSITIVE", label: "긍정" },
    { key: "NEGATIVE", label: "부정" },
    { key: "NEUTRAL", label: "중립" },
  ],
  widthHint: "굵을수록 점수가 높고, 점선은 방향이 없는 관계입니다",
  mixTitle: "무엇으로 계산하나 — 뉴스 · 공시 비율",
  mixHint: "이 비율로 관계 점수를 다시 섞습니다. 한쪽 근거가 없는 관계는 있는 쪽 점수를 그대로 씁니다",
  mixScope: "이 탭에서만 유지되고, 다시 접속하면 50 : 50 으로 돌아갑니다",
  mixReset: "50 : 50",
  minScore: "최소 점수",
  altitude: "주가 고도",
  altitudeTitle: "기간 등락률만큼 행성을 원반 위·아래로 띄웁니다",
  altitudeHint: "위쪽 = 상승, 아래쪽 = 하락 (선택 기간 등락률)",
  advanced: "고급",
  topOnly: "상위만",
  topOnlyTitle: "켜면 점수 상위 관계만 기본 표시, 나머지는 기업을 호버할 때 나타납니다",
  nameLabels: "기업 이름표",
  edgeLabels: "관계 종류 글자",
  edgeLabelsTitle: "선 중간에 공급·투자·협력·경쟁 글자를 붙입니다 (상위 관계만)",
};

/** 기업 툴팁의 한 줄 힌트 — 조작법 표(CONTROL_HINTS)의 클릭·더블클릭 항목과 같은 문구 */
export const TIP_NODE_HINT = "클릭 = 미리보기 · 더블클릭 = 관계망";

/* ---- 기업 미리보기 (은하 뷰에서 행성 클릭) ---- */
export const PREVIEW_UI = {
  warp: "관계망 보기",
  warpTitle: "이 기업을 중심으로 관계망을 다시 배치합니다 (행성 더블클릭과 같음)",
  steps: "연결 단계",
  hops: (h1: number, h2: number) => `1홉 ${h1} · 2홉 ${h2}`,
  noLinks: "지금 필터 안에서는 이어진 기업이 없습니다.",
  pickTitle: "이 기업을 미리보기로",
  pathHint: "다른 행성에 커서를 올리면 이 기업에서 가는 경로가 위에 표시됩니다.",
};

/* ---- 공간 참조 구조 (P0-6) ---- */
export const RINGS_UI = {
  /** 허브 랭크 링 라벨 — 반지름 랭크 상위 비율 */
  hub: (pct: number) => `허브 상위 ${pct}%`,
  hubPercents: [10, 30, 60],
  zeroPlane: "등락률 0%",
  /** 기업 중심 뷰 depth 1·2·3 궤도 */
  depth: ["직접 관계 (1다리)", "2다리 건너", "3다리 건너"],
};

/* ---- 관계 툴팁 3단 (P0-4) · 자동 추출 고지 (P0-8) · 근거 타임라인 (P0-9) ---- */
export const EVIDENCE_UI = {
  autoExtracted: "자동 추출됨",
  autoExtractedHelp: "이 관계는 뉴스·공시 문장에서 자동으로 추출했습니다. 신뢰도와 근거 건수를 함께 확인하세요.",
  lowConfidence: "신뢰 낮음",
  /** 이 아래(0~1)면 신뢰 낮음 표시 */
  lowConfidenceBelow: 0.4,
  confidence: (pctText: string) => `신뢰 ${pctText}`,
  noEvidence: "근거 뉴스 없음 · 점수는 누적 데이터 기반",
  hoverMore: (n: number) => `근거 ${n}건 · 클릭해서 전체 보기`,
  hoverCapped: (n: number) => `근거 ${n}건 · 클릭해서 보기`,
  timelineKicker: "근거 뉴스",
  timelineOrder: "시간순",
  timelineSummary: (n: number, latest: string) => `최근 근거 ${n}건 · 가장 최근 ${latest}`,
  representative: "대표 근거",
  contribution: "기여",
  copy: "문장 복사",
  copied: "근거 문장을 복사했습니다",
  original: "원문",
  disclosure: "공시는 점수에 반영되지만 원문 문장은 공개하지 않습니다",
  more: "더 보기",
  /** 오래된 근거의 불투명도 — 숨기지 않고 물러나게만 */
  ageOpacity: [
    { maxDays: 30, opacity: 1 },
    { maxDays: 90, opacity: 0.75 },
    { maxDays: Infinity, opacity: 0.55 },
  ],
};

/* ---- 뜻밖의 관계 — 이 제품의 핵심 경험 ---- */
export const SURPRISE_UI = {
  mark: "✦",
  badge: "뜻밖",
  kicker: "뜻밖의 관계",
  title: "생각지 못한 연결",
  titleFor: (name: string) => `${name}의 뜻밖의 관계`,
  lead: "산업·시장이 다르거나, 둘 다 허브가 아니거나, 경쟁하면서도 손잡은 — 예상 밖의 연결만 골랐습니다.",
  hoverHint: "커서를 올리면 은하에서 그 선이 밝아지고, 누르면 근거를 봅니다",
  why: "왜 뜻밖인가",
  empty: "지금 필터 안에는 뜻밖이라 할 만한 관계가 없습니다. 최소 점수를 낮추거나 관계 종류를 더 켜 보세요.",
  emptyCompany: "이 기업의 관계는 모두 같은 산업·시장 안에 있습니다.",
  collapse: "접기",
  expandTag: (n: number) => `${n}개`,
  tooltip: (labels: string[]) => `뜻밖 · ${labels.join(" · ")}`,
};

export interface HelpSection {
  id: string;
  title: string;
  /** 목차의 한 줄 */
  summary: string;
  body: string[];
  /** 이 절을 "화면에서 보기" 로 재생할 때 시작할 투어 단계 */
  tourStep?: TourStepId;
}

/** `?` 도움말 허브의 절 — 기획서 §9 목차와 같은 순서 */
export const HELP_SECTIONS: HelpSection[] = [
  {
    id: "what",
    title: "코스모스는 무엇을 보여주나",
    summary: "뉴스·공시에서 자동 추출한 기업 간 관계를 3D 지도로",
    body: [PRODUCT.definition, PRODUCT.metaphor, "관계는 기사·공시 문장에서 기계가 뽑은 결과라 틀릴 수 있습니다. 점수와 함께 근거 건수를 확인하세요."],
    tourStep: "intro",
  },
  {
    id: "place",
    title: "은하 읽는 법 — 자리",
    summary: "거리=관계 강도, 안쪽=허브, 높이=주가 등락",
    body: [
      "두 행성이 가까울수록 관계가 강합니다. 자리는 산업이 아니라 관계가 정합니다.",
      "중심에 가까울수록 연결이 많은 기업(허브)입니다.",
      "주가 고도를 켜면 행성이 선택 기간 등락률만큼 원반 위(상승)·아래(하락)로 떠 있습니다.",
      "행성 크기는 연결 가중치와 시가총액, 색은 산업입니다.",
    ],
    tourStep: "place",
  },
  {
    id: "beam",
    title: "관계선 읽는 법",
    summary: "굵기=점수, 색=영향 방향, 점선=방향 없음",
    body: [
      "굵을수록 점수(0~100)가 높습니다.",
      "색은 충격이 좋은 쪽(파랑)·나쁜 쪽(빨강)·중립(보라)으로 전이되는지를 뜻합니다. 관계 종류의 색이 아닙니다.",
      "점선은 방향이 없는 관계(협력·경쟁)입니다. 방향이 있는 관계(공급·투자)는 구슬이 출발 기업에서 상대 기업으로 흐릅니다.",
    ],
    tourStep: "beam",
  },
  {
    id: "why",
    title: "왜 이어졌는지 찾기",
    summary: "커서를 올리면 한 줄, 누르면 근거 뉴스",
    body: [
      "관계선에 커서를 올리면 종류 설명 → 양쪽 기업·점수·영향·신뢰 → 대표 근거 문장 한 줄이 차례로 뜹니다.",
      "관계선을 누르면 오른쪽 패널에 양쪽 기업, 기간별 점수, 근거 뉴스 타임라인이 열립니다. 문장은 복사해 쓸 수 있습니다.",
      "관계는 자동 추출입니다 — 패널의 '자동 추출됨' 표시와 신뢰도를 함께 보세요. 신뢰도가 낮으면 주황으로 표시됩니다.",
      "근거 뉴스가 없는 관계는 패널에 그렇게 표시됩니다. 공시는 점수에 반영되지만 원문 문장은 공개하지 않습니다.",
    ],
    tourStep: "hover",
  },
  {
    id: "surprise",
    title: "뜻밖의 관계 찾기",
    summary: "예상 밖의 연결만 골라 보여 주는 카드",
    body: [
      "왼쪽 '뜻밖의 관계' 카드는 산업이나 시장이 다른 두 기업, 둘 다 허브가 아닌 조합, 경쟁하면서도 협력·공급하는 관계처럼 예상 밖의 연결을 점수로 골라 보여 줍니다.",
      "행에 커서를 올리면 은하에서 그 선이 밝아지고, 누르면 근거 뉴스가 열립니다. 선 위의 ✦ 표시도 같은 뜻입니다.",
      "기업 관계망 안에서는 그 기업 기준의 뜻밖의 관계만 남습니다.",
    ],
  },
  {
    id: "system",
    title: "기업 관계망으로 들어가기",
    summary: "클릭→워프, 궤도=몇 다리 건너인지, ESC로 복귀",
    body: [
      "행성을 한 번 누르면 오른쪽에 그 기업의 뉴스와 연결 단계(1홉 이웃을 역할별로, 2홉까지 닿는 기업 수)가 뜹니다. 이 상태에서 다른 행성에 커서를 올리면 두 기업 사이 경로가 위에 표시됩니다.",
      "행성을 두 번 누르거나 패널의 '관계망 보기'를 누르면 그 기업을 중심으로 관계망이 다시 배치됩니다.",
      "가운데가 선택한 기업, 첫 궤도가 직접 관계(1다리), 바깥 궤도가 2·3다리 건넌 기업입니다.",
      "기업에 커서를 올리면 이웃에 역할(공급사·고객·투자자·피투자·협력·경쟁) 태그가 붙고, 상단 요약에서 역할별로 걸러 볼 수 있습니다.",
      "ESC 또는 왼쪽 위 ← 로 은하에 돌아옵니다.",
    ],
    tourStep: "warp",
  },
  {
    id: "controls",
    title: "조작법",
    summary: "회전 · 확대 · 이동 · 경로 · 검색 · 카메라",
    body: CONTROL_HINTS.map((h) => `${h.keys} — ${h.does}`).concat(CAMERA_PRESETS.map((p) => `카메라 ${p.label} — ${p.hint}`)),
  },
  {
    id: "filters",
    title: "필터와 화면 조절",
    summary: "관계 종류 · 뉴스·공시 비율 · 최소 점수 · 상위만 · 주가 고도",
    body: [
      "오른쪽 아래 카드에서 관계 종류를 끄고 켜고, 최소 점수를 올려 약한 관계를 숨길 수 있습니다.",
      "'뉴스 · 공시 비율' 을 움직이면 그 비율로 관계 점수를 다시 섞어 선의 굵기와 밝기가 바뀝니다. 한쪽 근거가 없는 관계는 있는 쪽 점수를 그대로 쓰고, 양쪽 다 없으면 선을 그리지 않습니다. 이 비율은 이 탭에서만 유지됩니다.",
      "'상위만' 을 켜면 점수 상위 관계만 기본 표시되고 나머지는 기업에 커서를 올릴 때 나타납니다.",
      "'주가 고도' 와 기간(1D·1M·3M)은 행성 높이의 기준을 바꿉니다.",
      "위쪽 '산업' 에서 한 산업만 남길 수 있습니다.",
    ],
  },
  {
    id: "mine",
    title: "내 은하와 개인화",
    summary: "관심 기업만으로 만든 개인 은하",
    body: [
      "로그인하면 위쪽 '내 은하' 로 관심 기업과 그 1홉 관계만 같은 배치로 볼 수 있습니다.",
      "기업 패널의 ☆ 로 관심 기업을 담고, 검색으로 바로 추가할 수도 있습니다.",
    ],
  },
  {
    id: "elsewhere",
    title: "뉴스·기업 화면과의 관계",
    summary: "뉴스의 2D 관계 지도, 기업 디렉터리와 투자 트리",
    body: ["뉴스 탭은 선택한 기사가 언급한 기업과 1홉 이웃을 2D 지도로, 영향도 순위와 근거 문장과 함께 보여 줍니다.", "기업 탭은 관심 기업, 디렉터리(검색·시장·산업), 투자·지분 관계 트리입니다. 어디서든 기업을 누르면 이 은하의 관계망으로 옵니다."],
  },
  {
    id: "limits",
    title: "데이터의 한계와 자주 묻는 질문",
    summary: "자동 추출 · 스냅샷 갱신 주기 · 과거 시점",
    body: [
      "관계는 자동 추출이라 틀릴 수 있습니다. 근거 건수가 적은 관계는 조심해서 읽으세요.",
      "그래프는 매시 갱신되는 공개 스냅샷입니다. 왼쪽 아래 카드에서 기준 시각과 다음 갱신 시각을 볼 수 있습니다.",
      "과거 시점의 은하를 되감는 기능은 아직 없습니다.",
      "공시는 점수에 반영되지만 원문 문장은 공개하지 않습니다.",
    ],
  },
];
