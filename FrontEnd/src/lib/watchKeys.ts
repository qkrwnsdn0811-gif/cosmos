/**
 * 관심 기업 등록·해제 뒤 다시 받을 쿼리 — watched 값을 담은 기업 상세·목록·검색만.
 * 시세판(quotes)·관계 기업(related)·주가(prices)·지표(metrics, fundamental)는 관심과 무관해서 다시 받지 않는다
 * (별을 누를 때마다 61종목 시세와 1년 일봉을 다시 받던 것을 막는다).
 */
const UNAFFECTED = new Set(["metrics", "metrics-history", "prices", "quotes", "related", "fundamental"]);

export function holdsWatchState(queryKey: readonly unknown[]): boolean {
  const [root, second] = queryKey;
  if (root !== "company" && root !== "companies") return false;
  return !UNAFFECTED.has(String(second));
}
