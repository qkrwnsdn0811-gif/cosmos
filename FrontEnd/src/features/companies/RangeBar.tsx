import { fmtPrice, rangePosition } from "./quoteFormat";

/** 저가—고가 막대와 현재 위치 (토스증권 머리 줄의 1일 범위·52주 범위) */
export default function RangeBar({ label, low, high, value }: { label: string; low: number | null; high: number | null; value: number | null }) {
  if (low == null || high == null || value == null) return null;
  const pos = rangePosition(low, high, value);
  return (
    <div className="rb" aria-label={`${label}: 최저 ${fmtPrice(low)}, 최고 ${fmtPrice(high)}, 현재 ${fmtPrice(value)}`}>
      <span className="rb-label">{label}</span>
      <span className="rb-low num">{fmtPrice(low)}</span>
      <span className="rb-track" aria-hidden="true">
        <i style={{ left: `${pos * 100}%` }} />
      </span>
      <span className="rb-high num">{fmtPrice(high)}</span>
    </div>
  );
}
