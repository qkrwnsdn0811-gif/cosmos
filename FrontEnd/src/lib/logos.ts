import files from "./logo-codes.json";

/** public/company-logos 에 준비된 로고 파일. scripts/fetch-logos.mjs 가 logo-codes.json 을 갱신한다. */
const LOGO_FILES = new Map<string, string>();
(files as string[]).forEach((f) => {
  const code = f.replace(/\.[a-z]+$/, "");
  // png 를 우선한다
  if (!LOGO_FILES.has(code) || f.endsWith(".png")) LOGO_FILES.set(code, f);
});
export const LOGO_CODES = new Set(LOGO_FILES.keys());

export function logoUrl(stockCode: string | undefined | null) {
  const f = stockCode ? LOGO_FILES.get(stockCode) : undefined;
  return f ? `/company-logos/${f}` : null;
}
