import { useState } from "react";
import type { Horizon } from "@/api/types";
import { useBacktest } from "@/lib/queries";
import { accuracyText, fmtPct, HORIZONS } from "./investCopy";

/**
 * 적중률 공개(F11) — 학습 기간(2026-06까지) 뒤 뉴스에서 딱지 방향이 실제 초과수익률 부호와 맞은 비율과,
 * 무조건 많은 쪽으로 답했을 때의 기준선을 나란히 보여 준다. 기준선보다 낫지 않으면 그렇다고 말한다.
 */
export default function AccuracyBanner({ horizon }: { horizon: Horizon }) {
  const backtest = useBacktest("SEMICONDUCTOR");
  const [open, setOpen] = useState(false);
  const rows = backtest.data?.rows ?? [];
  const overall = rows.find((r) => r.horizon === horizon && r.newsType === null) ?? null;
  const text = accuracyText(overall);
  if (backtest.isLoading || !backtest.data) return null;
  const period = backtest.data.testStart && backtest.data.testEnd ? `${backtest.data.testStart} ~ ${backtest.data.testEnd}` : "학습 기간 뒤";
  const byType = rows.filter((r) => r.horizon === horizon && r.newsType !== null && r.labeledCount >= 20);

  return (
    <section className={`invest-accuracy ${text?.beats ? "beats" : ""}`} aria-label="딱지 적중률">
      <div className="row between wrap" style={{ gap: 8 }}>
        <div>
          <span className="kick">적중률 공개 · {HORIZONS[horizon].label}</span>
          <div className="invest-accuracy-line">
            {text ? text.line : "아직 검증할 딱지가 없습니다."}
          </div>
          <div className="meta">
            {period} 뉴스로 검증 ·{" "}
            {text && !text.beats
              ? "아직 기준선보다 낫지 않습니다. 딱지는 과거 경향일 뿐 예측 근거가 아닙니다."
              : "통계는 2026-06까지 뉴스로만 만들었습니다."}
          </div>
        </div>
        {byType.length > 0 && (
          <button type="button" className="btn btn-g btn-xs" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "유형별 접기" : "유형별 보기"}
          </button>
        )}
      </div>
      {open && (
        <table className="invest-accuracy-table">
          <thead>
            <tr>
              <th>뉴스 유형</th>
              <th>딱지</th>
              <th>적중률</th>
              <th>기준선</th>
            </tr>
          </thead>
          <tbody>
            {byType.map((r) => (
              <tr key={r.newsType}>
                <td>{r.newsTypeLabel ?? r.newsType}</td>
                <td className="num">{r.labeledCount.toLocaleString("ko-KR")}</td>
                <td className="num">{fmtPct(r.hitRate, 1, false)}</td>
                <td className="num">{fmtPct(r.baselineRate, 1, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
