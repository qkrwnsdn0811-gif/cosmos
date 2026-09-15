import type { CosmosApi } from "./contract";
import { liveApi } from "./live";
import { mockApi } from "@/mock/mockApi";

export const API_MODE: "mock" | "live" = import.meta.env.VITE_API_MODE === "live" ? "live" : "mock";

/** 화면 코드는 항상 이 객체만 사용한다. */
export const api: CosmosApi = API_MODE === "live" ? liveApi : mockApi;

export { ApiError } from "./client";
export type * from "./types";
