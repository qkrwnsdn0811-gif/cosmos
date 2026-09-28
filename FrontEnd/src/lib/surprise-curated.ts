/**
 * Special Link 고정 목록 — 시연용.
 *
 * 비어 있지 않으면 lib/surprise 의 자동 점수 계산을 건너뛰고 여기 적힌 기업 쌍만 Special Link 로 보여 준다
 * (레일·✦ 표식·툴팁·관계 패널의 "왜 특별한가" 전부). 순서는 이 배열 순서 그대로다.
 * 같은 쌍에 관계선이 여러 개(예: 협력 + 경쟁)면 점수가 가장 높은 선 하나를 쓴다.
 * 그래프 스냅샷에 그 쌍의 관계선이 없으면 그 줄은 조용히 빠진다.
 *
 * 자동 계산으로 되돌리려면 배열을 비우면 된다 (`[]`).
 * companyId 는 운영 DB 기준 (2026-09-27 확인).
 */
export interface CuratedSpecialLink {
  /** 두 기업 companyId. 순서는 상관없다 */
  a: string;
  b: string;
  /** 칩에 보이는 짧은 라벨 */
  label: string;
  /** 툴팁·관계 패널에 보이는 한 줄 설명 */
  detail: string;
}

export const CURATED_SPECIAL_LINKS: CuratedSpecialLink[] = [
  {
    a: "67b97638-e7f3-52e4-aff6-3e1c0d6bc666", // LG전자
    b: "b57ff1e3-59d9-5a16-9814-64927b1288cc", // Marriott International
    label: "전자 ↔ 호텔",
    detail: "차세대 스마트호텔 플랫폼을 공동 개발하고 북미 호텔에 우선 적용 — 전자제품 기업과 호텔 체인의 예상하기 어려운 연결",
  },
  {
    a: "2c4f6e73-9966-5d16-8020-56524d3a6d0e", // LG에너지솔루션
    b: "11ec9b95-ed7c-54f9-b17d-6ac868e93cbe", // Nvidia
    label: "배터리 ↔ AI 인프라",
    detail: "Nvidia AI 데이터센터 생태계의 BESS(에너지저장장치) 파트너로 참여 — 배터리와 AI 인프라가 연결되는 산업 확장",
  },
  {
    a: "a708dcdb-7795-5958-b80a-8ae5a46098c3", // 신한지주
    b: "e0ebbeb4-4b2b-58a8-b0fa-217ef2721736", // 카카오
    label: "금융 ↔ 플랫폼",
    detail: "카카오 컨소시엄의 금융 AI 서비스 파트너로 신한은행 참여 — 금융과 플랫폼 기업의 AI 협력",
  },
  {
    a: "2f19c5bc-02f9-507b-9991-ffa93791a2f9", // Amazon
    b: "4d263ce6-c2cd-5e68-b324-09688cdb9abe", // Qualcomm
    label: "클라우드 ↔ 반도체",
    detail: "AWS용 맞춤형 AI 반도체와 광연결 기술 공동 개발 — 클라우드와 반도체 기업의 인프라 협력",
  },
  {
    a: "2faad581-a170-5ee9-9f08-de9306546512", // 현대모비스
    b: "a8255526-f3a4-5130-a85c-1a2598190694", // 하나금융지주
    label: "자동차 ↔ 금융",
    detail: "협력사 금융지원을 위한 상생보증 업무협약 체결 — 자동차 공급망과 금융 지원이 연결되는 의외의 관계",
  },
];
