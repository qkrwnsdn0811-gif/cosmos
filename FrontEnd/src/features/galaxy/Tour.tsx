import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SceneModel } from "@/lib/graph";
import { CONTROL_HINTS, TOUR_BUTTONS, TOUR_DONE_KEY, TOUR_DONE_TOAST, TOUR_FALLBACK, TOUR_HOVER_HINT_MS, TOUR_HOVER_TIMEOUT_MS, TOUR_INTRO_KICKER, TOUR_INTRO_MS, TOUR_ORDER, TOUR_STEPS, type TourStepId } from "@/lib/guide";
import { useGalaxy } from "@/store/galaxy";
import { useUi } from "@/store/ui";
import { Icon } from "@/components/ui";
import { tourAnchor, type TourTarget } from "@/three/tourState";

interface Props {
  /** 지금 은하 뷰에 올라간 모델 — 표본(가장 굵은 빔·연결이 가장 많은 기업)을 여기서 고른다 */
  model: SceneModel | null;
  /** 첫 방문 자동 시작 허용 — 딥링크(?company=)·내 은하(?scope=mine)로 들어온 진입은 끈다 */
  autoStart: boolean;
}

/** 씬 안 대상은 TourAnchor 가 투영하고, HUD 요소(검색바)는 여기서 DOM 사각형을 직접 읽는다 */
type Target = TourTarget | { kind: "dom"; selector: string };
const SEARCH_TARGET: Target = { kind: "dom", selector: ".search-trigger" };

export function isTourDone() {
  try {
    return localStorage.getItem(TOUR_DONE_KEY) === "1";
  } catch {
    // 저장 불가 환경(프라이빗 모드 등)에서는 매번 뜨지 않게 "본 것" 으로 친다
    return true;
  }
}
function markTourDone() {
  try {
    localStorage.setItem(TOUR_DONE_KEY, "1");
  } catch {
    /* 다음 방문에 다시 보인다 */
  }
}

/** 4단계 캡션의 조작 요약 — 드래그·휠·WASD 와 클릭/더블클릭의 차이 */
const WARP_KEYS = CONTROL_HINTS.filter((h) => ["드래그", "휠", "W A S D", "클릭", "더블클릭"].includes(h.keys));

/**
 * 첫 방문 투어 — 실제 렌더된 은하 위에 스포트라이트를 뚫고 4단계 캡션으로 읽는 법을 가르친다 (기획서 §3).
 * - intro: 제품 정의 1문장 (씬 인트로 비행과 겹쳐 보인다) → place: 원반 전체 → beam: 가장 굵은 빔 → hover: 연결 많은 기업, 사용자가
 *   실제로 빔을 호버하면 진행(6s 뒤 커서 고스트, 12s 뒤 자동) → warp: 행성 하나 + 조작 요약
 * - 딤은 클릭·호버를 통과시킨다. 그래야 3단계에서 진짜 툴팁이 뜬다. ESC·건너뛰기·워프 시작은 모두 "끝" 이다
 * - 표본이 없는 희소 스냅샷은 캡션을 바꾸고 검색바를 가리킨다 (콜드스타트)
 * - 문구는 전부 lib/guide 에서 온다 — `?` 허브와 같은 문장
 */
export default function Tour({ model, autoStart }: Props) {
  const phase = useGalaxy((s) => s.phase);
  const scope = useGalaxy((s) => s.scope);
  const hoveredEdgeId = useGalaxy((s) => s.hoveredEdgeId);
  const tourRequest = useUi((s) => s.tourRequest);
  const toast = useUi((s) => s.toast);

  const [step, setStep] = useState<TourStepId | null>(null);
  /** 커서 고스트를 띄운 단계 — 지금 단계와 같을 때만 보인다 (단계가 바뀌면 따로 끄지 않아도 사라진다) */
  const [hintStep, setHintStep] = useState<TourStepId | null>(null);
  const hoverHint = hintStep !== null && hintStep === step;
  const dimRef = useRef<HTMLDivElement>(null);
  const ghostRef = useRef<HTMLDivElement>(null);
  /** 허브에서 들어온 재생 요청 — 은하 뷰·모델이 준비되면 그 단계에서 시작한다 */
  const pending = useRef<TourStepId | null>(null);
  const seenRequest = useRef(tourRequest.nonce);
  const autoTried = useRef(false);
  /** 3단계에 들어온 순간 이미 올려 둔 호버 — 그것은 "실제로 호버했다" 로 치지 않는다 (비었다가 새로 생기는 전이만 인정) */
  const hoverAtEntry = useRef<string | null>(null);

  // 표본 — 점수 60 이상 중 가장 굵은 빔, 연결이 가장 많은 기업. for-of 로 쓴다 (forEach 콜백 안 대입은 TS 가 좁힌 타입을 풀지 않는다)
  const sample = useMemo(() => {
    if (!model) return null;
    let beamId: string | null = null;
    let beamScore = -1;
    for (const e of model.edges) {
      if (e.score >= TOUR_FALLBACK.beamMinScore && e.score > beamScore) {
        beamScore = e.score;
        beamId = e.id;
      }
    }
    let hubId: string | null = null;
    let hubDegree = -1;
    for (const n of model.nodes) {
      if (n.degree > hubDegree) {
        hubDegree = n.degree;
        hubId = n.id;
      }
    }
    return { beamId, hubId: hubDegree >= TOUR_FALLBACK.hoverMinDegree ? hubId : null, planetId: hubId };
  }, [model]);

  const sparse = step === "beam" ? !sample?.beamId : step === "hover" ? !sample?.hubId : false;
  const target = useMemo<Target | null>(() => {
    if (!step || !sample) return null;
    switch (step) {
      case "place":
        return { kind: "disc" };
      case "beam":
        return sample.beamId ? { kind: "edge", id: sample.beamId } : SEARCH_TARGET;
      case "hover":
        return sample.hubId ? { kind: "node", id: sample.hubId } : SEARCH_TARGET;
      case "warp":
        return sample.planetId ? { kind: "node", id: sample.planetId } : null;
      default:
        return null;
    }
  }, [step, sample]);
  const copy = useMemo(() => {
    if (!step) return null;
    const base = TOUR_STEPS[step];
    if (step === "beam" && sparse) return { ...base, ...TOUR_FALLBACK.beamSparse };
    if (step === "hover" && sparse) return { ...base, ...TOUR_FALLBACK.hoverSparse };
    return base;
  }, [step, sparse]);

  const finish = useCallback(() => {
    setStep(null);
    markTourDone();
    toast(TOUR_DONE_TOAST);
  }, [toast]);
  const next = () => {
    if (!step) return;
    if (step === "warp") {
      finish();
      return;
    }
    setStep(TOUR_ORDER[TOUR_ORDER.indexOf(step) + 1]);
  };

  // 시작 — 허브의 재생 요청은 대기열에 두고(기업 중심 뷰라면 은하로 먼저 돌아온다), 은하 뷰·모델이 준비되면 그 단계에서 시작한다.
  // 요청이 없으면 첫 방문 자동 시작 조건을 한 번만 본다. 반영은 다음 틱 — 씬이 한 프레임 그린 뒤 구멍을 뚫고, 효과 본문에서 상태를 바로 바꾸지 않는다
  useEffect(() => {
    if (tourRequest.nonce !== seenRequest.current) {
      seenRequest.current = tourRequest.nonce;
      pending.current = tourRequest.step ?? "intro";
      const g = useGalaxy.getState();
      if (g.focusId && g.phase !== "warp") g.backToGalaxy();
    }
    // 요청이 기업으로 가는 워프 도중에 왔으면 위 분기가 못 돌린다 — 착지해 system 이 된 시점에 다시 은하로 돌린다
    if (pending.current && phase === "system") {
      useGalaxy.getState().backToGalaxy();
      return;
    }
    if (!model || phase !== "galaxy") return;
    const id = window.setTimeout(() => {
      if (pending.current) {
        const s = pending.current;
        pending.current = null;
        setStep(s);
        return;
      }
      if (autoTried.current) return;
      autoTried.current = true;
      if (autoStart && scope === "all" && !isTourDone() && !useGalaxy.getState().focusId) setStep("intro");
    }, 0);
    return () => clearTimeout(id);
  }, [tourRequest, model, phase, scope, autoStart]);

  // 워프가 시작되면 투어를 접는다 — 마지막 단계의 클릭은 투어가 가리킨 바로 그 행동이고, 그 전이면 건너뛴 것이다. 어느 쪽이든 끝 (반영은 다음 틱)
  useEffect(() => {
    if (!step || phase === "galaxy") return;
    const id = window.setTimeout(finish, 0);
    return () => clearTimeout(id);
  }, [phase, step, finish]);

  // 단계별 시간 — intro 는 자동 진행, hover 는 힌트(6s)·자동 진행(12s)
  useEffect(() => {
    if (!step) return;
    const timers: number[] = [];
    if (step === "intro") timers.push(window.setTimeout(() => setStep("place"), TOUR_INTRO_MS));
    if (step === "hover" && !sparse) {
      timers.push(window.setTimeout(() => setHintStep("hover"), TOUR_HOVER_HINT_MS));
      timers.push(window.setTimeout(() => setStep("warp"), TOUR_HOVER_TIMEOUT_MS));
    }
    return () => timers.forEach((t) => clearTimeout(t));
  }, [step, sparse]);

  // 3단계 진입 순간의 호버 값을 기억한다 — 앞 단계에서부터 선 위에 있던 커서로 즉시 통과하지 않게
  useEffect(() => {
    if (step === "hover") hoverAtEntry.current = useGalaxy.getState().hoveredEdgeId;
  }, [step]);
  // 3단계: 실제로 빔을 호버하면 진행 — 툴팁을 읽을 시간을 조금 준다. 어느 빔이든 좋다: 캡션은 "선 위에" 라고만 말하고,
  // 딤 밖의 빔도 진짜 툴팁을 띄우므로 배우는 내용은 같다
  useEffect(() => {
    if (step !== "hover") return;
    if (!hoveredEdgeId) {
      hoverAtEntry.current = null;
      return;
    }
    if (hoveredEdgeId === hoverAtEntry.current) return;
    const t = window.setTimeout(() => setStep("warp"), 900);
    return () => clearTimeout(t);
  }, [step, hoveredEdgeId]);

  // ESC = 건너뛰기. 도움말 허브가 위에 열려 있으면 그 ESC 는 허브를 닫는 것이고 투어는 남는다.
  // 캡처 단계에 건다 — GalaxyPage 의 ESC 체인(버블, 먼저 등록)이 closeHelp() 로 helpOpen 을 먼저 false 로 바꾸기 전에 판단해야 한다
  useEffect(() => {
    if (!step) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !useUi.getState().helpOpen) finish();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [step, finish]);

  // 스포트라이트 좌표 — 씬 대상은 TourAnchor 가 쓴 값을, HUD 대상은 DOM 사각형을 rAF 로 읽어 CSS 변수에 쓴다 (리렌더 없음)
  useEffect(() => {
    if (!step) {
      tourAnchor.target = null;
      return;
    }
    tourAnchor.target = target && target.kind !== "dom" ? target : null;
    let raf = 0;
    const tick = () => {
      const el = dimRef.current;
      const host = el?.parentElement;
      if (el && host) {
        let x = -999;
        let y = -999;
        let r = 0;
        if (target?.kind === "dom") {
          const t = document.querySelector(target.selector);
          if (t) {
            const a = t.getBoundingClientRect();
            const b = host.getBoundingClientRect();
            x = a.left - b.left + a.width / 2;
            y = a.top - b.top + a.height / 2;
            r = Math.hypot(a.width, a.height) / 2 + 14;
          }
        } else if (target && tourAnchor.visible) {
          x = tourAnchor.x;
          y = tourAnchor.y;
          r = tourAnchor.r;
        }
        el.style.setProperty("--x", `${x.toFixed(1)}px`);
        el.style.setProperty("--y", `${y.toFixed(1)}px`);
        el.style.setProperty("--r", `${r.toFixed(1)}px`);
        const g = ghostRef.current;
        if (g) {
          g.style.left = `${x.toFixed(1)}px`;
          g.style.top = `${y.toFixed(1)}px`;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      tourAnchor.target = null;
    };
  }, [step, target]);

  if (!step || !copy) return null;
  return (
    <>
      <div ref={dimRef} className={`tour-dim${step === "intro" ? " full" : ""}`} aria-hidden="true" />
      {hoverHint && (
        <div ref={ghostRef} className="tour-ghost" aria-hidden="true">
          <span>
            <Icon.Cursor />
          </span>
        </div>
      )}
      {step === "intro" ? (
        <div className="tour-intro" role="dialog" aria-live="polite" aria-label="COSMOS 소개">
          <span className="kick">{TOUR_INTRO_KICKER}</span>
          <b>{copy.title}</b>
          {copy.body.map((line) => (
            <p key={line}>{line}</p>
          ))}
          <button type="button" className="tour-skip" onClick={finish}>
            {TOUR_BUTTONS.skip}
          </button>
        </div>
      ) : (
        <div className="tour-card card" role="dialog" aria-live="polite" aria-label={`안내 ${copy.index} / 4`}>
          <span className="kick">{copy.index} / 4</span>
          <b>{copy.title}</b>
          {copy.body.map((line) => (
            <p key={line}>{line}</p>
          ))}
          {step === "warp" && (
            <div className="tour-keys">
              {WARP_KEYS.map((h) => (
                <span key={h.keys}>
                  <kbd>{h.keys}</kbd> {h.does}
                </span>
              ))}
            </div>
          )}
          <div className="tour-actions">
            <button type="button" className="btn btn-g btn-sm" onClick={finish}>
              {TOUR_BUTTONS.skip}
            </button>
            {copy.primary && (
              <button type="button" className="btn btn-p btn-sm" onClick={next}>
                {copy.primary}
              </button>
            )}
          </div>
        </div>
      )}
    </>
  );
}
