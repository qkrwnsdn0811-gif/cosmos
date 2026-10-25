import type { CompanyQuote, MarketIndex } from "@/api/types";
import Sparkline from "@/components/Sparkline";
import { fmtPct, tone } from "./quoteFormat";

const INDEX_NAME: Record<string, string> = { KOSPI: "코스피", KOSDAQ: "코스닥" };

/** 지수 카드: 지수 값, 전일 대비(포인트·%), 1개월 등락, 최근 3개월 미니 차트 (Google Finance 시장 요약 자리) */
function IndexCard({ index }: { index: MarketIndex }) {
  const prev = index.change1d != null ? index.close / (1 + index.change1d) : null;
  const point = prev != null ? index.close - prev : null;
  const t = tone(index.change1d);
  return (
    <div className="mk-card">
      <div className="mk-name">{INDEX_NAME[index.indexCode] ?? index.indexCode}</div>
      <div className="mk-value num">{index.close.toLocaleString("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
      <div className={`mk-change num tone-${t}`}>
        {point != null && `${point > 0 ? "+" : point < 0 ? "−" : ""}${Math.abs(point).toFixed(2)} `}({fmtPct(index.change1d)})
      </div>
      <div className="mk-foot">
        <span className="meta">
          1개월 <b className={`num tone-${tone(index.change1m)}`}>{fmtPct(index.change1m, 1)}</b>
        </span>
        <Sparkline values={index.points.map((p) => p.close)} width={110} height={30} label={`${INDEX_NAME[index.indexCode]} 최근 3개월`} />
      </div>
    </div>
  );
}

/** 반도체 종목 등락 분포: 마지막 거래일에 오른·보합·내린 종목 수 */
function BreadthCard({ rows, asOf }: { rows: CompanyQuote[]; asOf: string | null }) {
  const priced = rows.filter((r) => r.change1d != null);
  const up = priced.filter((r) => (r.change1d ?? 0) > 0).length;
  const down = priced.filter((r) => (r.change1d ?? 0) < 0).length;
  const flat = priced.length - up - down;
  const pctOf = (n: number) => (priced.length ? (n / priced.length) * 100 : 0);
  return (
    <div className="mk-card mk-breadth">
      <div className="mk-name">반도체 {priced.length}종목 · {asOf ? `${Number(asOf.slice(5, 7))}월 ${Number(asOf.slice(8, 10))}일` : "-"}</div>
      <div className="mk-breadth-nums num">
        <span className="tone-rise">상승 {up}</span>
        <span className="meta">보합 {flat}</span>
        <span className="tone-fall">하락 {down}</span>
      </div>
      <div className="mk-bar" role="img" aria-label={`상승 ${up}, 보합 ${flat}, 하락 ${down}`}>
        <i className="rise" style={{ width: `${pctOf(up)}%` }} />
        <i className="flat" style={{ width: `${pctOf(flat)}%` }} />
        <i className="fall" style={{ width: `${pctOf(down)}%` }} />
      </div>
    </div>
  );
}

/** 시세판 맨 위 시장 띠. 지수를 못 받으면 지수 카드만 빠진다 */
export default function MarketStrip({ indices, rows, asOf }: { indices: MarketIndex[]; rows: CompanyQuote[]; asOf: string | null }) {
  return (
    <section className="mk-strip" aria-label="시장 요약">
      {indices.map((i) => (
        <IndexCard key={i.indexCode} index={i} />
      ))}
      <BreadthCard rows={rows} asOf={asOf} />
    </section>
  );
}
