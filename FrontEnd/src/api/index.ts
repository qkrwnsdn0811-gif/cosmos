import type { CosmosApi } from "./contract";
import { liveApi } from "./live";

/**
 * 화면 코드는 항상 이 객체만 사용한다. 구현은 실 백엔드(/api) 하나다 —
 * 브라우저 내 목업(src/mock)은 2026-09-16 백엔드 연동과 함께 제거했다.
 */
export const api: CosmosApi = liveApi;

export { ApiError } from "./client";
export type * from "./types";
