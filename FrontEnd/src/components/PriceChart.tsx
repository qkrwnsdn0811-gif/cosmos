import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { Candle } from "@/api/types";
import { fmtShortDate } from "@/lib/format";

export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setW(entries[0].contentRect.width));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

interface Props {
  data: Candle[];
  height?: number;
  color?: string;
  showAxis?: boolean;
}

/** 종가 라인 + 영역 그라디언트. 최고·최저를 라벨로 표시한다. */
export default function PriceChart({ data, height = 150, color = "#829CFF", showAxis = true }: Props) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const padX = 6;
  const padTop = 18;
  const padBottom = showAxis ? 22 : 8;

  const geo = useMemo(() => {
    if (!width || data.length < 2) return null;
    const closes = data.map((d) => d.closePrice);
    const min = Math.min(...closes);
    const max = Math.max(...closes);
    const span = max - min || 1;
    const innerW = width - padX * 2;
    const innerH = height - padTop - padBottom;
    const x = (i: number) => padX + (i / (data.length - 1)) * innerW;
    const y = (v: number) => padTop + (1 - (v - min) / span) * innerH;
    const pts = closes.map((c, i) => [x(i), y(c)] as const);
    const line = pts.map(([px, py], i) => `${i === 0 ? "M" : "L"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
    const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${(height - padBottom).toFixed(1)} L${pts[0][0].toFixed(1)},${(height - padBottom).toFixed(1)} Z`;
    const iMin = closes.indexOf(min);
    const iMax = closes.indexOf(max);
    return { line, area, min, max, iMin, iMax, x, y, ticks: [0, Math.floor((data.length - 1) / 2), data.length - 1] };
  }, [data, width, height, padBottom]);

  const up = data.length > 1 && data[data.length - 1].closePrice >= data[0].closePrice;
  const stroke = color === "auto" ? (up ? "#4FE8C0" : "#FF8FA3") : color;
  const gid = useId();

  return (
    <div ref={ref} style={{ width: "100%", height }}>
      {geo && (
        <svg width={width} height={height} role="img" aria-label="주가 추이">
          <defs>
            <linearGradient id={gid} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.32" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={geo.area} fill={`url(#${gid})`} />
          <path d={geo.line} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          <circle cx={geo.x(geo.iMax)} cy={geo.y(geo.max)} r="3" fill={stroke} />
          <circle cx={geo.x(geo.iMin)} cy={geo.y(geo.min)} r="3" fill={stroke} />
          <text x={Math.min(width - 4, Math.max(4, geo.x(geo.iMax)))} y={geo.y(geo.max) - 7} fontSize="11" fill="#AAB3C2" textAnchor={geo.iMax > data.length / 2 ? "end" : "start"} fontVariant="tabular-nums">
            {geo.max.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
          </text>
          <text x={Math.min(width - 4, Math.max(4, geo.x(geo.iMin)))} y={geo.y(geo.min) + 14} fontSize="11" fill="#AAB3C2" textAnchor={geo.iMin > data.length / 2 ? "end" : "start"} fontVariant="tabular-nums">
            {geo.min.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}
          </text>
          {showAxis &&
            geo.ticks.map((i, k) => (
              <text key={i} x={geo.x(i)} y={height - 6} fontSize="11" fill="#8B95A1" textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"}>
                {/* 거래일은 브라우저 시간대로 옮기면 하루가 밀린다 — UTC 로 날짜 성분을 그대로 읽는다 */}
                {fmtShortDate(data[i].tradingAt, "UTC")}
              </text>
            ))}
        </svg>
      )}
    </div>
  );
}

/** 지표 이력용 작은 막대 스파크라인 */
export function Sparkbars({ values, color = "#829CFF", height = 36 }: { values: number[]; color?: string; height?: number }) {
  const max = Math.max(1, ...values);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height }} aria-hidden="true">
      {values.map((v, i) => (
        <span key={i} style={{ flex: 1, height: `${Math.max(4, (v / max) * 100)}%`, background: color, opacity: 0.35 + (v / max) * 0.65, borderRadius: 2 }} />
      ))}
    </div>
  );
}
