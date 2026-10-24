import { useMemo, useState, type PointerEvent } from "react";
import type { ChartPoint } from "@/api/types";
import { ErrorNotice, Skeleton } from "@/components/ui";
import { useEventChart } from "@/lib/queries";
import { fmtPct } from "./investCopy";

/** 종목선(식별 색)과 지수선(무채색 점선 기준선) — 어두운 패널(#101622)에서 구분·대비 검사를 통과한 조합 */
const STOCK = "#829cff";
const INDEX = "#6b7588";
const W = 600;
const H = 190;
const PAD = { l: 44, r: 92, t: 14, b: 26 };

/**
 * 뉴스 전후 주가 차트(F7) — 뉴스 5거래일 전부터 20거래일 뒤까지, 뉴스 전날 종가 대비 누적 수익률을 종목과 시장 지수로 함께 그린다.
 * 축은 하나(누적 수익률 %)이고, 두 선의 차이가 시장 대비 초과수익률이다. 마우스를 올리면 그날의 세 값을 보여 준다.
 */
export default function PriceChart({ eventId, companyId, name }: { eventId: string; companyId: string; name: string }) {
  const chart = useEventChart(eventId, companyId);
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    const pts = chart.data?.points ?? [];
    const values = pts.flatMap((p) => [p.stockReturn, p.indexReturn]).filter((v): v is number => v != null);
    if (!pts.length || !values.length) return null;
    const lo = Math.min(0, ...values);
    const hi = Math.max(0, ...values);
    const span = hi - lo || 0.01;
    const y0 = lo - span * 0.08;
    const y1 = hi + span * 0.08;
    const first = pts[0].offset;
    const last = pts[pts.length - 1].offset;
    const x = (o: number) => PAD.l + ((o - first) / Math.max(1, last - first)) * (W - PAD.l - PAD.r);
    const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b);
    const line = (get: (p: ChartPoint) => number | null) => {
      let d = "";
      let pen = false;
      for (const p of pts) {
        const v = get(p);
        if (v == null) {
          pen = false;
          continue;
        }
        d += `${pen ? "L" : "M"}${x(p.offset).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      }
      return d;
    };
    const step = span > 0.2 ? 0.1 : span > 0.08 ? 0.05 : span > 0.03 ? 0.02 : 0.01;
    const ticks: number[] = [];
    for (let t = Math.ceil(y0 / step) * step; t <= y1; t += step) ticks.push(Math.round(t * 1000) / 1000);
    const lastWith = (get: (p: ChartPoint) => number | null) => [...pts].reverse().find((p) => get(p) != null) ?? null;
    return { pts, x, y, ticks, stock: line((p) => p.stockReturn), index: line((p) => p.indexReturn), lastStock: lastWith((p) => p.stockReturn), lastIndex: lastWith((p) => p.indexReturn), first, last };
  }, [chart.data]);

  if (chart.isLoading) return <Skeleton h={H} style={{ borderRadius: 12 }} />;
  if (chart.isError) return <ErrorNotice message="주가 차트를 불러오지 못했습니다." onRetry={() => chart.refetch()} />;
  if (!geo || !chart.data) return <p className="invest-muted">이 뉴스 전후의 주가 데이터가 아직 없습니다.</p>;

  const bench = chart.data.benchmark === "KOSDAQ" ? "코스닥" : "코스피";
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * W;
    let best = geo.pts[0];
    for (const p of geo.pts) if (Math.abs(geo.x(p.offset) - sx) < Math.abs(geo.x(best.offset) - sx)) best = p;
    setHover(best.offset);
  };
  const hp = hover == null ? null : geo.pts.find((p) => p.offset === hover) ?? null;
  // 끝 라벨이 겹치면 아래쪽 라벨을 내린다
  const ls = geo.lastStock?.stockReturn != null ? geo.y(geo.lastStock.stockReturn) : null;
  let li = geo.lastIndex?.indexReturn != null ? geo.y(geo.lastIndex.indexReturn) : null;
  if (ls != null && li != null && Math.abs(ls - li) < 14) li = ls + (li >= ls ? 14 : -14);

  return (
    <figure className="invest-chart">
      <figcaption className="row between">
        <span className="invest-chart-title">뉴스 전 5거래일 ~ 뒤 20거래일 누적 수익률</span>
        <span className="invest-legend" aria-hidden="true">
          <i style={{ borderTopColor: STOCK }} /> {name}
          <i className="dash" style={{ borderTopColor: INDEX }} /> {bench} 지수
        </span>
      </figcaption>
      <div className="invest-chart-stage">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${name}과 ${bench} 지수의 뉴스 전후 누적 수익률`}>
          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.l} x2={W - PAD.r} y1={geo.y(t)} y2={geo.y(t)} className={t === 0 ? "zero" : "grid"} />
              <text x={PAD.l - 6} y={geo.y(t) + 4} textAnchor="end" className="tick">
                {fmtPct(t, 0)}
              </text>
            </g>
          ))}
          <line x1={geo.x(0)} x2={geo.x(0)} y1={PAD.t} y2={H - PAD.b} className="newsday" />
          <text x={geo.x(0) + 4} y={PAD.t + 10} className="tick">
            뉴스 날
          </text>
          {[geo.first, 5, 10, geo.last].filter((o, i, a) => o >= geo.first && o <= geo.last && a.indexOf(o) === i && o !== 0).map((o) => (
            <text key={o} x={geo.x(o)} y={H - 8} textAnchor="middle" className="tick">
              {o > 0 ? `+${o}일` : `${o}일`}
            </text>
          ))}
          <path d={geo.index} fill="none" stroke={INDEX} strokeWidth={2} strokeDasharray="5 4" strokeLinecap="round" />
          <path d={geo.stock} fill="none" stroke={STOCK} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
          {ls != null && geo.lastStock && (
            <text x={W - PAD.r + 8} y={ls + 4} className="end-label">
              종목 {fmtPct(geo.lastStock.stockReturn)}
            </text>
          )}
          {li != null && geo.lastIndex && (
            <text x={W - PAD.r + 8} y={li + 4} className="end-label muted">
              지수 {fmtPct(geo.lastIndex.indexReturn)}
            </text>
          )}
          {hp && (
            <g pointerEvents="none">
              <line x1={geo.x(hp.offset)} x2={geo.x(hp.offset)} y1={PAD.t} y2={H - PAD.b} className="crosshair" />
              {hp.indexReturn != null && <circle cx={geo.x(hp.offset)} cy={geo.y(hp.indexReturn)} r={4} fill={INDEX} className="dot" />}
              {hp.stockReturn != null && <circle cx={geo.x(hp.offset)} cy={geo.y(hp.stockReturn)} r={4} fill={STOCK} className="dot" />}
            </g>
          )}
          {/* 표식보다 넓은 히트 영역 — 선 위가 아니라 그래프 전체에서 가장 가까운 날을 잡는다 */}
          <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} fill="transparent" onPointerMove={onMove} onPointerLeave={() => setHover(null)} />
        </svg>
        {hp && (
          <div className="invest-tip" style={{ left: `${(geo.x(hp.offset) / W) * 100}%` }} role="status">
            <b>{hp.date}</b> {hp.offset === 0 ? "(뉴스 날)" : hp.offset > 0 ? `(+${hp.offset}거래일)` : `(${hp.offset}거래일)`}
            <span>종목 {fmtPct(hp.stockReturn)}</span>
            <span>지수 {fmtPct(hp.indexReturn)}</span>
            <span>초과 {fmtPct(hp.abnormalReturn)}</span>
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>{name} 뉴스 전후 누적 수익률</caption>
        <thead>
          <tr>
            <th>날짜</th>
            <th>종목</th>
            <th>지수</th>
            <th>초과수익률</th>
          </tr>
        </thead>
        <tbody>
          {geo.pts.map((p) => (
            <tr key={p.offset}>
              <td>{p.date}</td>
              <td>{fmtPct(p.stockReturn)}</td>
              <td>{fmtPct(p.indexReturn)}</td>
              <td>{fmtPct(p.abnormalReturn)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
