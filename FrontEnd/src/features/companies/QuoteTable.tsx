import type { CompanyQuote } from "@/api/types";
import { CompanyAvatar, Icon } from "@/components/ui";
import Sparkline from "@/components/Sparkline";
import { SIZE_LABEL, fmtCap, fmtPct, fmtPrice, fmtRatio, fmtSigned, perText, tone, type SortDir, type SortKey } from "./quoteFormat";

export interface SortState {
  key: SortKey;
  dir: SortDir;
}

/** 열 머리. sort 가 있으면 누를 때 정렬, small = 좁은 화면에서 숨김 (토스 '주식 골라보기' 표의 열 구성) */
const COLUMNS: { label: string; sort?: SortKey; right?: boolean; small?: boolean; first?: SortDir }[] = [
  { label: "순위", right: true, small: true },
  { label: "", small: true },
  { label: "기업", sort: "name", first: "asc" },
  { label: "현재가", right: true },
  { label: "등락", sort: "change1d", right: true },
  { label: "1개월 추이" },
  { label: "1개월", sort: "change1m", right: true, small: true },
  { label: "시가총액", sort: "cap", right: true, small: true },
  { label: "PER", sort: "per", right: true, small: true, first: "asc" },
  { label: "PBR", sort: "pbr", right: true, small: true, first: "asc" },
  { label: "배당", sort: "dividend", right: true, small: true },
  { label: "관계", sort: "relations", right: true, small: true },
];

export default function QuoteTable({
  rows,
  sort,
  onSort,
  watched,
  onToggleWatch,
  onOpen,
}: {
  rows: CompanyQuote[];
  sort: SortState;
  onSort: (next: SortState) => void;
  watched: (companyId: string) => boolean;
  onToggleWatch: (companyId: string, watched: boolean) => void;
  onOpen: (companyId: string) => void;
}) {
  const clickHead = (key: SortKey, first: SortDir = "desc") =>
    onSort(sort.key === key ? { key, dir: sort.dir === "desc" ? "asc" : "desc" } : { key, dir: first });

  return (
    <div style={{ overflowX: "auto" }}>
      <table className="dir-table qt">
        <thead>
          <tr>
            {COLUMNS.map((c, i) => (
              <th
                key={i}
                className={[c.right && "right", c.small && "hide-sm", c.sort && "sortable", c.sort && sort.key === c.sort && "on"].filter(Boolean).join(" ")}
                onClick={c.sort ? () => clickHead(c.sort!, c.first) : undefined}
                aria-sort={c.sort && sort.key === c.sort ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
              >
                {c.label}
                {c.sort && <span className="sort-arrow">{sort.key === c.sort ? (sort.dir === "desc" ? "▼" : "▲") : "▲▼"}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((q, i) => {
            const on = watched(q.companyId);
            const amount = q.close != null && q.prevClose != null ? q.close - q.prevClose : null;
            const t = tone(q.change1d);
            return (
              <tr key={q.companyId} className="row-btn" tabIndex={0} onClick={() => onOpen(q.companyId)} onKeyDown={(e) => e.key === "Enter" && onOpen(q.companyId)}>
                <td className="right hide-sm num qt-rank">{i + 1}</td>
                <td className="hide-sm qt-star">
                  <button
                    type="button"
                    className={`star-btn ${on ? "on" : ""}`}
                    aria-label={on ? `${q.name} 관심 해제` : `${q.name} 관심 등록`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleWatch(q.companyId, on);
                    }}
                  >
                    <Icon.Star filled={on} />
                  </button>
                </td>
                <td>
                  <div className="co">
                    <CompanyAvatar name={q.name} companyId={q.companyId} stockCode={q.stockCode} market={q.market} industry={q.industry} size={32} />
                    <div>
                      <b>{q.name}</b>
                      <span className="meta num">{[q.stockCode, q.industry].filter(Boolean).join(" · ")}</span>
                    </div>
                  </div>
                </td>
                <td className="right num qt-price">{fmtPrice(q.close)}</td>
                <td className={`right num tone-${t}`}>
                  <span className="qt-chg">{fmtPct(q.change1d)}</span>
                  <span className="qt-amt">{fmtSigned(amount)}</span>
                </td>
                <td className="qt-spark">
                  <Sparkline values={q.spark} label={`${q.name} 최근 1개월 종가`} />
                </td>
                <td className={`right num hide-sm tone-${tone(q.change1m)}`}>{fmtPct(q.change1m, 1)}</td>
                <td className="right num hide-sm">
                  {fmtCap(q.marketCap)}
                  {q.sizeClass && <span className={`size-badge size-${q.sizeClass.toLowerCase()}`}>{SIZE_LABEL[q.sizeClass]}</span>}
                </td>
                <td className={`right num hide-sm ${perText(q) === "적자" ? "qt-loss" : ""}`}>{perText(q)}</td>
                <td className="right num hide-sm">{q.pbr != null ? `${q.pbr.toFixed(2)}배` : "-"}</td>
                <td className="right num hide-sm">{q.dividendYield ? fmtRatio(q.dividendYield) : "-"}</td>
                <td className="right num hide-sm">
                  {q.relationCount}
                  <span className="unit">곳</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
