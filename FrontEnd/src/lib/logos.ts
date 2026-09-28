import { useSyncExternalStore } from "react";
import type { Market } from "@/api/types";
import files from "./logo-codes.json";

/**
 * 기업 로고 조회.
 *
 * 로고 식별자는 종목코드가 아니라 `시장 + 종목코드` 다 — 같은 코드가 시장마다 다른 기업일 수 있어서,
 * 파일을 `KOSPI-005930.png` 처럼 시장을 붙여 둘 수 있고 그 파일이 있으면 먼저 쓴다.
 * 시장 없이 코드만 붙인 기존 파일(`005930.png`)은 모든 시장의 공통 폴백으로 남는다.
 *
 * 종목코드는 끝까지 문자열로 다룬다 — 숫자로 바꾸면 국내 코드의 앞 0(`005930` → `5930`)이 날아가고
 * 영문이 섞인 코드(`0126Z0`)는 아예 값이 깨진다.
 */

/** 파일명 스템(대문자) → 파일명. scripts/fetch-logos.mjs 가 logo-codes.json 을 갱신한다. */
const LOGO_FILES = new Map<string, string>();
(files as string[]).forEach((f) => {
  const stem = f.replace(/\.[a-z0-9]+$/i, "").toUpperCase();
  // png 를 우선한다
  if (!LOGO_FILES.has(stem) || f.endsWith(".png")) LOGO_FILES.set(stem, f);
});
export const LOGO_CODES = new Set(LOGO_FILES.keys());

/** 종목코드 정규화 — 앞뒤 공백만 걷어내고 대문자로 맞춘다 (미국 티커는 대문자 관리, 국내 코드는 그대로) */
export function normalizeStockCode(stockCode: string | null | undefined): string | null {
  const code = stockCode?.trim().toUpperCase();
  return code ? code : null;
}

/** 로고 키 — 장기적으로 `${market}:${stockCode}` 가 기업을 가리키는 안전한 키다 */
export function logoKey(market: Market | null | undefined, stockCode: string | null | undefined): string | null {
  const code = normalizeStockCode(stockCode);
  if (!code) return null;
  const m = market?.trim().toUpperCase();
  return m ? `${m}:${code}` : code;
}

/** 시장별 파일 → 코드만 있는 파일 순으로 찾는다. 없으면 null 이고 화면은 이니셜로 떨어진다. */
export function logoUrl(stockCode: string | null | undefined, market?: Market | null): string | null {
  const code = normalizeStockCode(stockCode);
  if (!code) return null;
  const m = market?.trim().toUpperCase();
  const file = (m ? LOGO_FILES.get(`${m}-${code}`) : undefined) ?? LOGO_FILES.get(code);
  return file ? `/company-logos/${encodeURIComponent(file)}` : null;
}

/* --------------------------- companyId → 로고 참조 --------------------------- */
/**
 * 기업 중심 그래프(`/graphs/companies/{id}`)나 뉴스의 관련 기업 응답은 `companyId`·`name` 만 준다.
 * 그 화면에서도 로고를 그릴 수 있도록, 시장·종목코드를 함께 주는 응답(기업 목록·상세·관심 목록·최신 그래프)이
 * 지나갈 때 api 경계에서 한 번 적어 둔다 (src/api/live.ts). 캐시가 비어 있으면 그냥 이니셜로 떨어진다.
 */
export interface CompanyLogoRef {
  market: Market | null;
  stockCode: string | null;
}

const REFS = new Map<string, CompanyLogoRef>();
const listeners = new Set<() => void>();

export function rememberCompanyLogoRef(c: { companyId?: string | null; market?: Market | null; stockCode?: string | null } | null | undefined) {
  if (!c?.companyId || !c.stockCode) return false;
  const prev = REFS.get(c.companyId);
  if (prev && prev.stockCode === c.stockCode && prev.market === (c.market ?? null)) return false;
  REFS.set(c.companyId, { market: c.market ?? null, stockCode: c.stockCode });
  return true;
}

/** 목록·그래프 응답을 통째로 넘긴다. 실제로 새로 적힌 게 있을 때만 구독자에게 알린다. */
export function rememberCompanyLogoRefs(list: readonly ({ companyId?: string | null; market?: Market | null; stockCode?: string | null } | null | undefined)[]) {
  let changed = false;
  list.forEach((c) => {
    if (rememberCompanyLogoRef(c)) changed = true;
  });
  if (changed) listeners.forEach((fn) => fn());
}

const subscribe = (fn: () => void) => {
  listeners.add(fn);
  return () => void listeners.delete(fn);
};

/** 시장·종목코드를 모르는 화면(뉴스 관련 기업 등)이 companyId 로 참조를 찾는다. 나중에 채워지면 다시 그린다. */
export function useCompanyLogoRef(companyId: string | null | undefined): CompanyLogoRef | undefined {
  // 같은 값이면 같은 객체를 돌려주므로(REFS 갱신 시에만 교체) useSyncExternalStore 가 무한 렌더로 돌지 않는다
  return useSyncExternalStore(
    subscribe,
    () => (companyId ? REFS.get(companyId) : undefined),
    () => undefined,
  );
}
