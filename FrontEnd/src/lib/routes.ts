/**
 * 화면 경로. 첫 화면(/)은 뉴스 → 영향 종목 피드이고, 3D 기업 관계 은하는 탐색 화면(/galaxy)으로 옮겼다 (2026-10-25, 명세서 F10).
 * 은하의 기업 중심 뷰·내 은하·관계 선택은 같은 경로에 쿼리로 붙는다.
 */
export const HOME_PATH = "/";
export const GALAXY_PATH = "/galaxy";

export const galaxyCompanyPath = (companyId: string) => `${GALAXY_PATH}?company=${companyId}`;
export const galaxyEdgePath = (relationshipId: string) => `${GALAXY_PATH}?edge=${relationshipId}`;
export const GALAXY_MINE_PATH = `${GALAXY_PATH}?scope=mine`;
export const eventMapPath = (eventId: string) => `/events?event=${eventId}`;
