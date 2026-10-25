import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Horizon } from "@/api/types";
import { CompanyAvatar, Icon, Skeleton } from "@/components/ui";
import { useBacktest, useCompanyQuotes, useToggleWatch, useWatchedSet } from "@/lib/queries";
import { companyPath } from "@/lib/routes";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { fmtPct, fmtPrice, tone } from "../companies/quoteFormat";
import { gradesHold } from "../invest/investCopy";
import {
  HORIZON_QUESTION,
  SCORED_QUESTIONS,
  TYPE_INFO,
  investorType,
  matchBasis,
  matchStocks,
  saveMatch,
  type SavedMatch,
} from "./investorMatch";

const STEPS = SCORED_QUESTIONS.length + 1;
const HORIZON_LABEL: Record<Horizon, string> = { SHORT: "단기", LONG: "장기" };

/**
 * 투자 성향 간이 진단(질문 5개) → 성향·기간에 맞는 반도체 종목. 지난 결과가 있으면 결과부터 연다.
 * 종목은 별표로 관심 등록하고, 관심 종목은 첫 화면 피드 위 알림으로 이어진다.
 */
export default function MatchDialog({
  initial,
  onSaved,
  onClose,
}: {
  initial: SavedMatch | null;
  onSaved: (match: SavedMatch) => void;
  onClose: () => void;
}) {
  const [result, setResult] = useState<SavedMatch | null>(initial);
  const [step, setStep] = useState(0);
  const [scores, setScores] = useState<number[]>([]);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // 별표에서 띄운 로그인 창이 위에 있으면 Esc 는 그 창만 닫는다
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !useUi.getState().authDialog && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // 질문이 바뀌면 첫 선택지로 초점을 옮긴다 — 키보드만으로도 끝까지 답할 수 있게
  useEffect(() => {
    panel.current?.querySelector<HTMLButtonElement>(".md-opt")?.focus();
  }, [step, result]);

  const answer = (score: number) => {
    setScores([...scores.slice(0, step), score]);
    setStep(step + 1);
  };
  const chooseHorizon = (horizon: Horizon) => {
    const match = { type: investorType(scores), horizon };
    saveMatch(match);
    onSaved(match);
    setResult(match);
  };
  const restart = () => {
    setResult(null);
    setStep(0);
    setScores([]);
  };

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div ref={panel} className="card dialog md-dialog" role="dialog" aria-modal="true" aria-labelledby="md-title">
        <div className="row between">
          <span className="kick">투자 성향 맞춤</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>
        {result ? (
          <MatchResult match={result} onRestart={restart} onClose={onClose} />
        ) : step < SCORED_QUESTIONS.length ? (
          <Question
            step={step}
            text={SCORED_QUESTIONS[step].text}
            options={SCORED_QUESTIONS[step].options.map((o) => ({ label: o.label, on: scores[step] === o.score, pick: () => answer(o.score) }))}
            onBack={step > 0 ? () => setStep(step - 1) : undefined}
          />
        ) : (
          <Question
            step={step}
            text={HORIZON_QUESTION.text}
            options={HORIZON_QUESTION.options.map((o) => ({ label: o.label, on: false, pick: () => chooseHorizon(o.horizon) }))}
            onBack={() => setStep(step - 1)}
          />
        )}
      </div>
    </div>
  );
}

function Question({
  step,
  text,
  options,
  onBack,
}: {
  step: number;
  text: string;
  options: { label: string; on: boolean; pick: () => void }[];
  onBack?: () => void;
}) {
  return (
    <div className="md-quiz">
      {step === 0 && <p className="md-intro">질문 5개로 투자 성향을 간단히 알아보고, 성향에 맞는 반도체 종목을 골라 드려요.</p>}
      <div className="md-progress" aria-hidden="true">
        <i style={{ width: `${((step + 1) / STEPS) * 100}%` }} />
      </div>
      <span className="md-step num">
        {step + 1} / {STEPS}
      </span>
      <h2 id="md-title">{text}</h2>
      <div className="md-options">
        {options.map((o) => (
          <button key={o.label} type="button" className={`md-opt ${o.on ? "on" : ""}`} onClick={o.pick}>
            {o.label}
          </button>
        ))}
      </div>
      {onBack && (
        <button type="button" className="btn btn-g btn-sm md-back" onClick={onBack}>
          이전 질문
        </button>
      )}
    </div>
  );
}

function MatchResult({ match, onRestart, onClose }: { match: SavedMatch; onRestart: () => void; onClose: () => void }) {
  const quotes = useCompanyQuotes();
  const backtest = useBacktest("SEMICONDUCTOR");
  const watched = useWatchedSet();
  const toggle = useToggleWatch();
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const [busy, setBusy] = useState(false);

  // 검증 결과를 아직 못 받았으면 등급을 쓰지 않는다(지표 기준) — 받으면 다시 고른다
  const graded = backtest.data ? gradesHold(backtest.data.moveRows ?? [], match.horizon) : false;
  const picks = useMemo(
    () => (quotes.data ? matchStocks(quotes.data.items, match.type, match.horizon, graded) : []),
    [quotes.data, match, graded],
  );
  const info = TYPE_INFO[match.type];

  const toggleOne = (companyId: string, on: boolean) => {
    if (!authed) return openAuth("login");
    toggle.mutate(
      { companyId, watched: on },
      { onSuccess: (now) => now && toast("관심 기업에 담았어요. 첫 화면 피드 위 알림으로 이어서 볼 수 있어요.", "success") },
    );
  };
  const addAll = async () => {
    if (!authed) return openAuth("login");
    const todo = picks.filter((p) => !watched.has(p.quote.companyId));
    if (!todo.length) return;
    setBusy(true);
    try {
      for (const p of todo) await toggle.mutateAsync({ companyId: p.quote.companyId, watched: false });
      toast(`${todo.length}곳을 관심 기업에 담았어요. 첫 화면 피드 위 알림으로 이어서 볼 수 있어요.`, "success");
    } catch {
      toast("관심 등록을 다 하지 못했습니다. 잠시 후 다시 시도해 주세요.", "error");
    } finally {
      setBusy(false);
    }
  };
  const allWatched = picks.length > 0 && picks.every((p) => watched.has(p.quote.companyId));

  return (
    <div className="md-result">
      <h2 id="md-title">
        {info.label} <span className="md-horizon">· {HORIZON_LABEL[match.horizon]}</span>
      </h2>
      <p className="md-desc">{info.desc}</p>
      <p className="md-basis">
        <b>이렇게 골랐어요</b> {matchBasis(match.type, match.horizon, graded)}
      </p>
      {quotes.isLoading ? (
        <div className="md-list">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} h={58} />
          ))}
        </div>
      ) : quotes.isError ? (
        <p className="md-empty">시세를 불러오지 못했습니다. 잠시 후 다시 열어 주세요.</p>
      ) : picks.length === 0 ? (
        <p className="md-empty">지금 조건에 맞는 종목이 없어요. 기간을 바꿔 다시 진단해 보세요.</p>
      ) : (
        <ul className="md-list">
          {picks.map(({ quote: q, reasons }) => {
            const on = watched.has(q.companyId);
            return (
              <li key={q.companyId} className="md-row">
                <CompanyAvatar name={q.name} companyId={q.companyId} stockCode={q.stockCode} market={q.market} industry={q.industry} size={38} />
                <div className="md-main">
                  <div className="md-line">
                    <Link to={companyPath(q.companyId)} className="md-name" onClick={onClose}>
                      {q.name}
                    </Link>
                    <span className="md-price num">
                      {fmtPrice(q.close)} <span className={`md-${tone(q.change1d)}`}>{fmtPct(q.change1d)}</span>
                    </span>
                  </div>
                  <div className="md-chips">
                    {reasons.map((r) => (
                      <span key={r} className="md-chip">
                        {r}
                      </span>
                    ))}
                  </div>
                </div>
                <button
                  type="button"
                  className={`md-star ${on ? "on" : ""}`}
                  aria-label={on ? `${q.name} 관심 해제` : `${q.name} 관심 등록`}
                  onClick={() => toggleOne(q.companyId, on)}
                >
                  <Icon.Star filled={on} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="md-actions">
        <button type="button" className="btn btn-p btn-sm" onClick={addAll} disabled={busy || allWatched || picks.length === 0}>
          {allWatched ? "모두 관심 기업에 담겨 있어요" : busy ? "담는 중…" : "모두 관심 등록"}
        </button>
        <button type="button" className="btn btn-g btn-sm" onClick={onRestart}>
          다시 진단하기
        </button>
      </div>
      <p className="md-note">
        간이 성향 진단(참고용)입니다. 과거 통계와 현재 지표로 고른 목록이며 투자 권유가 아닙니다.
        {quotes.data?.asOf && ` 주가는 ${quotes.data.asOf} 종가 기준.`}
      </p>
    </div>
  );
}
