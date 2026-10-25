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
/** 고른 선택지를 보여 주는 시간 — 진행 바가 차오르는 것을 볼 만큼만 */
const PICK_MS = 260;
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
  // 고른 선택지를 잠깐 보여 준 뒤 넘긴다 — 그 사이 진행 바가 먼저 차오르고 다음 질문이 미끄러져 들어온다
  const [picked, setPicked] = useState<string | null>(null);
  const [dir, setDir] = useState<"next" | "back">("next");
  const timer = useRef<number | undefined>(undefined);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => () => window.clearTimeout(timer.current), []);

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

  /** 선택 표시 → PICK_MS 뒤 다음 화면. 기다리는 동안 다른 선택지는 받지 않는다 */
  const pick = (label: string, then: () => void) => {
    if (picked) return;
    setPicked(label);
    setDir("next");
    timer.current = window.setTimeout(() => {
      then();
      setPicked(null);
    }, PICK_MS);
  };
  const answer = (label: string, score: number) =>
    pick(label, () => {
      setScores([...scores.slice(0, step), score]);
      setStep(step + 1);
    });
  const chooseHorizon = (label: string, horizon: Horizon) =>
    pick(label, () => {
      const match = { type: investorType(scores), horizon };
      saveMatch(match);
      onSaved(match);
      setResult(match);
    });
  const back = () => {
    if (picked) return;
    setDir("back");
    setStep(step - 1);
  };
  const restart = () => {
    setDir("next");
    setResult(null);
    setStep(0);
    setScores([]);
  };
  // 진행 바: 지금 질문까지 + 방금 고른 것. 고르는 순간 먼저 차오른다
  const progress = Math.min(STEPS, step + 1 + (picked ? 1 : 0)) / STEPS;

  return (
    <div className="scrim md-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div ref={panel} className="card dialog md-dialog" role="dialog" aria-modal="true" aria-labelledby="md-title">
        <div className="row between">
          <span className="kick">투자 성향 맞춤</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>
        {result ? (
          <MatchResult match={result} onRestart={restart} onClose={onClose} />
        ) : (
          <div className="md-quiz">
            <p className="md-intro">질문 5개로 투자 성향을 간단히 알아보고, 성향에 맞는 반도체 종목을 골라 드려요.</p>
            {/* 바는 질문이 바뀌어도 같은 요소라 폭이 이어서 차오른다 */}
            <div className="md-progress" aria-hidden="true">
              <i style={{ width: `${progress * 100}%` }} />
            </div>
            {step < SCORED_QUESTIONS.length ? (
              <Question
                key={step}
                step={step}
                dir={dir}
                text={SCORED_QUESTIONS[step].text}
                options={SCORED_QUESTIONS[step].options.map((o) => ({
                  label: o.label,
                  on: picked ? picked === o.label : scores[step] === o.score,
                  pick: () => answer(o.label, o.score),
                }))}
                busy={picked != null}
                onBack={step > 0 ? back : undefined}
              />
            ) : (
              <Question
                key={step}
                step={step}
                dir={dir}
                text={HORIZON_QUESTION.text}
                options={HORIZON_QUESTION.options.map((o) => ({ label: o.label, on: picked === o.label, pick: () => chooseHorizon(o.label, o.horizon) }))}
                busy={picked != null}
                onBack={back}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** 질문 하나. key 가 바뀔 때마다 새로 붙어 dir 방향으로 미끄러져 들어온다 */
function Question({
  step,
  dir,
  text,
  options,
  busy,
  onBack,
}: {
  step: number;
  dir: "next" | "back";
  text: string;
  options: { label: string; on: boolean; pick: () => void }[];
  busy: boolean;
  onBack?: () => void;
}) {
  return (
    <div className={`md-q md-q-${dir} ${busy ? "leaving" : ""}`}>
      <span className="md-step num">
        {step + 1} / {STEPS}
      </span>
      <h2 id="md-title">{text}</h2>
      <div className="md-options">
        {options.map((o) => (
          <button key={o.label} type="button" className={`md-opt ${o.on ? "on" : ""}`} aria-pressed={o.on} onClick={o.pick} disabled={busy && !o.on}>
            {o.label}
          </button>
        ))}
      </div>
      {onBack && (
        <button type="button" className="btn btn-g btn-sm md-back" onClick={onBack} disabled={busy}>
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
