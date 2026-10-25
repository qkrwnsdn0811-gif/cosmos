import type { Horizon } from "@/api/types";
import { useBacktest } from "@/lib/queries";
import { HORIZONS, moveCheckText } from "./investCopy";

/**
 * 예상 변동폭 검증 — 학습 기간(2026-06까지) 뒤 뉴스로, '변동 큼'·'변동 작음' 종목이 실제로 얼마나 움직였는지 보여 준다.
 * 등급 순서가 실제와 맞은 기간이면 그렇다고, 아니면 숫자만 참고치라고 말한다. 오를지 내릴지가 아니라 얼마나 움직일지다.
 */
export default function MoveCheckBanner({ horizon }: { horizon: Horizon }) {
  const backtest = useBacktest("SEMICONDUCTOR");
  if (backtest.isLoading || !backtest.data) return null;
  const move = moveCheckText(backtest.data.moveRows ?? [], horizon);
  if (!move) return null;
  const period = backtest.data.testStart && backtest.data.testEnd ? `${backtest.data.testStart} ~ ${backtest.data.testEnd}` : "학습 기간 뒤";

  return (
    <section className={`invest-accuracy ${move.holds ? "holds" : ""}`} aria-label="예상 변동폭 검증">
      <span className="kick">
        예상 변동폭 검증 · {HORIZONS[horizon].label} · {period} 뉴스로 확인 (통계는 2026-06까지 뉴스로만 만들었습니다)
      </span>
      <div className="invest-accuracy-line">{move.line}</div>
      <div className="meta">
        {move.holds
          ? "등급대로 실제로도 크게·작게 움직였습니다. 오를지 내릴지가 아니라 얼마나 움직일지입니다."
          : "이 기간은 등급 순서가 실제와 맞지 않아 변동폭 숫자만 참고치로 보여 줍니다."}
      </div>
    </section>
  );
}
