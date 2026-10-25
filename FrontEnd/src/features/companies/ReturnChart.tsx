import { useMemo, useState, type PointerEvent } from "react";
import type { AlignedPoint } from "./quoteFormat";
import { fmtPct, fmtPrice, pctTickDigits, tone } from "./quoteFormat";

const INDEX = "#6b7588";
const W = 640;
const H = 240;
const PAD = { l: 52, r: 88, t: 14, b: 26 };
const TONE_COLOR = { rise: "var(--rise)", fall: "var(--fall)", flat: "var(--text-3)" } as const;

/** "2026-10-23" → "10.23" */
const md = (iso: string) => `${iso.slice(5, 7)}.${iso.slice(8, 10)}`;

function niceStep(span: number, count = 4): number {
  const raw = span / count;
  const pow = 10 ** Math.floor(Math.log10(raw || 1));
  const n = raw / pow;
  return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * pow;
}

/**
 * 기업 상세 차트 (Google Finance 의 '비교' 차트 자리).
 * compare: 종목과 소속 시장 지수의 기간 누적 수익률 두 선(지수는 회색 점선). price: 종가 선과 영역, 기간 첫날 종가 점선.
 * 선 색은 기간 등락으로 정한다(상승 빨강·하락 파랑). 마우스를 올리면 가장 가까운 거래일의 값을 보여 준다.
 */
export default function ReturnChart({ points, mode, name, benchName }: { points: AlignedPoint[]; mode: "compare" | "price"; name: string; benchName: string }) {
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    if (points.length < 2) return null;
    const values = mode === "compare" ? points.flatMap((p) => [p.stockReturn, p.indexReturn]) : points.map((p) => p.close);
    const lo = Math.min(...values, ...(mode === "compare" ? [0] : []));
    const hi = Math.max(...values, ...(mode === "compare" ? [0] : []));
    const span = hi - lo || Math.abs(hi) * 0.02 || 1;
    const y0 = lo - span * 0.08;
    const y1 = hi + span * 0.08;
    const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
    const path = (get: (p: AlignedPoint) => number) => points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(get(p)).toFixed(1)}`).join("");
    const step = niceStep(y1 - y0);
    const ticks: number[] = [];
    for (let t = Math.ceil(y0 / step) * step; t <= y1 + 1e-12; t += step) ticks.push(Number(t.toPrecision(12)));
    const main = mode === "compare" ? path((p) => p.stockReturn) : path((p) => p.close);
    const area = mode === "price" ? `${main}L${x(points.length - 1).toFixed(1)},${H - PAD.b}L${x(0).toFixed(1)},${H - PAD.b}Z` : null;
    // 점이 적으면 가운데와 끝이 겹친다 — 같은 자리는 한 번만
    const labels = [...new Set([0, Math.round((points.length - 1) / 2), points.length - 1])];
    return { x, y, ticks, step, main, area, index: mode === "compare" ? path((p) => p.indexReturn) : null, labels };
  }, [points, mode]);

  if (!geo) return <p className="invest-muted">이 기간의 주가 데이터가 부족합니다.</p>;

  const last = points[points.length - 1];
  const periodReturn = last.close / points[0].close - 1;
  const color = TONE_COLOR[tone(periodReturn)];
  const gradId = `rc-grad-${mode}`;
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * W;
    const i = Math.round(((sx - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };
  const hp = hover == null ? null : points[hover];
  let ls = geo.y(mode === "compare" ? last.stockReturn : last.close);
  let li = mode === "compare" ? geo.y(last.indexReturn) : null;
  if (li != null && Math.abs(ls - li) < 14) {
    if (li >= ls) li = ls + 14;
    else ls = li + 14;
  }

  return (
    <figure className="invest-chart rc">
      {mode === "compare" && (
        <figcaption className="row between">
          <span className="invest-chart-title">기간 누적 수익률</span>
          <span className="invest-legend" aria-hidden="true">
            <i style={{ borderTopColor: color }} /> {name}
            <i className="dash" style={{ borderTopColor: INDEX }} /> {benchName}
          </span>
        </figcaption>
      )}
      <div className="invest-chart-stage">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={mode === "compare" ? `${name}과 ${benchName}의 기간 누적 수익률` : `${name} 종가`}>
          <defs>
            <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.28} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} className={mode === "compare" && Math.abs(t) < 1e-12 ? "zero" : "grid"} />
              <text x={PAD.l - 6} y={geo.y(t) + 4} textAnchor="end" className="tick">
                {mode === "compare" ? fmtPct(t, pctTickDigits(geo.step)) : Math.round(t).toLocaleString("ko-KR")}
              </text>
            </g>
          ))}
          {mode === "price" && <line x1={PAD.l} x2={W - PAD.r} y1={geo.y(points[0].close)} y2={geo.y(points[0].close)} className="zero" strokeDasharray="3 4" />}
          {geo.labels.map((i) => (
            <text key={i} x={geo.x(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === points.length - 1 ? "end" : "middle"} className="tick">
              {md(points[i].date)}
            </text>
          ))}
          {geo.area && <path d={geo.area} fill={`url(#${gradId})`} />}
          {geo.index && <path d={geo.index} fill="none" stroke={INDEX} strokeWidth={2} strokeDasharray="5 4" strokeLinecap="round" />}
          <path d={geo.main} fill="none" stroke={color} strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" />
          <text x={W - PAD.r + 8} y={ls + 4} className="end-label" style={{ fill: color }}>
            {mode === "compare" ? `종목 ${fmtPct(last.stockReturn, 1)}` : fmtPrice(last.close)}
          </text>
          {li != null && (
            <text x={W - PAD.r + 8} y={li + 4} className="end-label muted">
              지수 {fmtPct(last.indexReturn, 1)}
            </text>
          )}
          {hp && hover != null && (
            <g pointerEvents="none">
              <line x1={geo.x(hover)} x2={geo.x(hover)} y1={PAD.t} y2={H - PAD.b} className="crosshair" />
              {mode === "compare" && <circle cx={geo.x(hover)} cy={geo.y(hp.indexReturn)} r={4} fill={INDEX} className="dot" />}
              <circle cx={geo.x(hover)} cy={geo.y(mode === "compare" ? hp.stockReturn : hp.close)} r={4} fill={color} className="dot" />
            </g>
          )}
          <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
        </svg>
        {hp && hover != null && (
          <div className="invest-tip" style={{ left: `${(geo.x(hover) / W) * 100}%` }} role="status">
            <b>{hp.date}</b>
            <span>종가 {fmtPrice(hp.close)}</span>
            <span>종목 {fmtPct(hp.stockReturn)}</span>
            {mode === "compare" && <span>지수 {fmtPct(hp.indexReturn)}</span>}
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>{name} 기간 종가와 누적 수익률</caption>
        <thead>
          <tr>
            <th>날짜</th>
            <th>종가</th>
            <th>종목 수익률</th>
            <th>{benchName} 수익률</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.date}>
              <td>{p.date}</td>
              <td>{fmtPrice(p.close)}</td>
              <td>{fmtPct(p.stockReturn)}</td>
              <td>{fmtPct(p.indexReturn)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
