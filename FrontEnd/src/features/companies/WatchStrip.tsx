import type { CompanyQuote } from "@/api/types";
import { CompanyAvatar } from "@/components/ui";
import { fmtPct, fmtPrice, tone, watchReasons, watchScore } from "./quoteFormat";

/** 줄에 보여 줄 최대 카드 수 — 나머지는 '주목 종목' 칩으로 표에서 본다 */
const MAX_CARDS = 5;

/** 주목 이유 칩 (금색) */
export function WatchChips({ q }: { q: CompanyQuote }) {
  return (
    <span className="watch-chips">
      {watchReasons(q).map((r) => (
        <span key={r.kind} className={`watch-chip watch-${r.kind}`}>
          {r.label}
        </span>
      ))}
    </span>
  );
}

/**
 * '지금 주목할 종목' — 뉴스 급증·큰 움직임·52주 최고·최저가 근처인 종목을 금색 테두리 카드로 (토스 '지금 뜨는 종목' 자리).
 * 이유만 사실대로 적고, 사라·오른다는 말하지 않는다
 */
export default function WatchStrip({ rows, onOpen, onShowAll }: { rows: CompanyQuote[]; onOpen: (companyId: string) => void; onShowAll: () => void }) {
  const picked = rows
    .map((q) => ({ q, score: watchScore(q) }))
    .filter((x): x is { q: CompanyQuote; score: number } => x.score != null)
    .sort((a, b) => b.score - a.score);
  if (!picked.length) return null;
  return (
    <section className="watch-strip" aria-label="지금 주목할 종목">
      <div className="row between wrap" style={{ gap: 8 }}>
        <h2 className="watch-title">
          <span className="watch-star" aria-hidden="true">
            ★
          </span>{" "}
          지금 주목할 종목 <span className="num">{picked.length}</span>
        </h2>
        <span className="meta">최근 7일 뉴스가 평소의 2배 이상 · 하루 ±5% 이상 · 52주 최고·최저가 5% 이내</span>
      </div>
      <div className="watch-cards">
        {picked.slice(0, MAX_CARDS).map(({ q }) => (
          <button key={q.companyId} type="button" className="watch-card" onClick={() => onOpen(q.companyId)}>
            <span className="row" style={{ gap: 8 }}>
              <CompanyAvatar name={q.name} companyId={q.companyId} stockCode={q.stockCode} market={q.market} industry={q.industry} size={28} />
              <b className="watch-name">{q.name}</b>
            </span>
            <span className="watch-price num">
              {fmtPrice(q.close)} <span className={`tone-${tone(q.change1d)}`}>{fmtPct(q.change1d)}</span>
            </span>
            <WatchChips q={q} />
          </button>
        ))}
        {picked.length > MAX_CARDS && (
          <button type="button" className="watch-card watch-more" onClick={onShowAll}>
            주목 종목 {picked.length}개 모두 보기 →
          </button>
        )}
      </div>
    </section>
  );
}
