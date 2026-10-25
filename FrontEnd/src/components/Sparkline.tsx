/**
 * 한 줄짜리 미니 차트. 마지막 값이 첫 값보다 크면 --rise(빨강), 작으면 --fall(파랑), 같으면 흐린 색.
 * 기업 탭(국내 서비스 관례대로 상승 빨강·하락 파랑)에서 쓴다. 값이 2개 미만이면 같은 크기의 빈 자리만 둔다.
 */
export default function Sparkline({
  values,
  width = 96,
  height = 28,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  /** 화면 읽기용 설명 (예: "최근 1개월 종가 추이") */
  label?: string;
}) {
  if (values.length < 2) return <span style={{ display: "inline-block", width, height }} aria-hidden />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pad = 2;
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => pad + (1 - (v - min) / span) * (height - pad * 2);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  const color = last > values[0] ? "var(--rise)" : last < values[0] ? "var(--fall)" : "var(--text-4)";
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true}>
      <line x1={pad} x2={width - pad} y1={y(values[0])} y2={y(values[0])} stroke="var(--text-4)" strokeOpacity={0.35} strokeDasharray="2 3" />
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
