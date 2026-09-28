import type { RelationshipType } from "@/api/types";
import { relationshipMeta } from "@/lib/meta";

/**
 * 장난감 우주 팔레트·상수 — three 를 import 하지 않는 순수 모듈.
 * HUD(components/ui · PathStrip · SurpriseRail · lib/spread)는 색과 반지름 계수만 필요한데, 예전에는 toon.ts 를 통해 three 라이브러리(1.2MB 청크)까지
 * 첫 로드 번들에 정적으로 끌어왔다. 씬 쪽(toon.ts)은 여기 값을 다시 내보내므로 씬 코드는 그대로 toon 에서 가져다 쓴다 (2026-09-18).
 */
export const LAB = {
  bg: "#0b1030",
  bgTop: "#1a2160",
  nebulaA: "#5b3fb8",
  nebulaB: "#2a5fd8",
  star: "#ffb74a",
  starHot: "#ff7a45",
  beam: "#ffa24c",
  beamGlow: "#ff7a45",
  /**
   * 영향 방향별 관계 빔 색 — 긍정 파랑·부정 빨강·중립 보라 (랩의 단색 beam 과 별개로 은하 본편이 쓴다).
   * 톤매핑이 없어 셰이더가 쓴 값이 그대로 화면 sRGB 가 되므로 셋 다 채도를 높게 유지하고,
   * 특히 파랑은 네이비 배경(#0b1030)에 묻히지 않도록 명도를 충분히 올려 잡았다.
   */
  /** 중립 톤 — 특정 관계를 가리키지 않는 자리(범례 글리프 기본값 등)에서 쓴다. 관계 유형 색은 lib/meta 가 정한다 */
  beamNeutral: "#a06bff",
  accent: "#6d8cff",
  planets: {
    teal: { name: "틸 — 대륙형", ocean: "#2fb8c9", oceanDeep: "#2394ad", land: "#7be495", landHi: "#b6f2c0", ring: "#bdedff" },
    violet: { name: "바이올렛 — 고리형", ocean: "#6e55e8", oceanDeep: "#5642c4", land: "#c48bff", landHi: "#e2c4ff", ring: "#e6d3ff" },
    blue: { name: "블루 — 위성형", ocean: "#3d7bff", oceanDeep: "#2f62d6", land: "#9dc4ff", landHi: "#d7e6ff", ring: "#d7e6ff" },
  },
} as const;

/**
 * 관계 유형 → 빔 색. 빔·화살촉·경로 스트립 글리프가 모두 이 한 곳을 보게 해서 색 규칙이 갈라지지 않게 한다.
 * 색 자체는 lib/meta 의 관계 유형 팔레트가 단일 소스다 — 선과 칩·태그가 같은 유형에 같은 색을 쓰게 하려는 것이다.
 * (예전에는 영향 방향(긍정·부정·중립)을 색으로 썼지만, 백엔드가 impactDirection 을 아직 채우지 않아 모든 선이
 *  중립 보라 한 색으로 보였다. 지금은 협력 파랑·경쟁 빨강·공급/투자 보라 계열로, 데이터가 있는 축을 색에 준다.)
 * 정의되지 않은 유형은 relationshipMeta 의 기본 회색으로 떨어진다.
 */
export function beamColorFor(type: RelationshipType | null | undefined): string {
  return relationshipMeta(type ?? "").color;
}

/**
 * 행성 반지름 = node.size × 이 계수 (월드 단위). 1.3 → 2.2 → 3.0 으로, "행성이 모래알만 하다" 는 지적에 따라 키웠다.
 * 레이아웃이 지키는 최소 간격은 (a.size + b.size) × 4.6 + 7 이므로, 가장 큰 두 행성이 붙어 서도 반지름 합이
 * 간격의 55% 안쪽이다(계수 4.6 을 넘기면 그때부터 닿는다). 정렬 격자의 칸 크기(lib/spread CELL_PER_SIZE)도
 * 이 값에서 끌어다 쓰므로 여기만 고치면 세 단계(은하·평면·정렬)가 같이 따라온다.
 * 간선 화살표·역할 링·투어 앵커·모프 고스트가 모두 행성 표면을 기준으로 놓이므로 이 값 하나만 보고 계산한다.
 */
export const PLANET_RADIUS_K = 3.0;
