import { useEffect, useRef, useState } from "react";
import { HELP_SECTIONS, HELP_UI, type TourStepId } from "@/lib/guide";
import { useGalaxy } from "@/store/galaxy";
import { useUi } from "@/store/ui";
import { Icon } from "@/components/ui";
import { isTyping, modalOpen } from "@/three/keyboard";

/** `?` 단축키 — 허브가 닫혀 있어도 살아 있어야 하므로 허브 본체와 떼어 항상 마운트한다. 검색(/)과 같은 규칙으로 양보 */
export function HelpHotkey() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "?" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (isTyping() || modalOpen() || useGalaxy.getState().searchOpen) return;
      e.preventDefault();
      useUi.getState().toggleHelp();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return null;
}

/**
 * `?` 도움말 허브 — 비모달 오버레이 (기획서 §6·§9). 씬은 뒤에서 그대로 움직이고, 절마다 "화면에서 보기" 로 그 단계의 투어를 재생한다.
 * 열려 있을 때만 마운트된다(GalaxyPage) — 열 때마다 요청된 절에서 새로 시작하고, 닫힌 상태의 잔여 상태가 없다.
 * 문구는 lib/guide HELP_SECTIONS 한 곳에서만 온다. ESC 는 GalaxyPage 의 한 겹 닫기 체인이 맡는다.
 */
export default function HelpHub() {
  const section = useUi((s) => s.helpSection);
  const closeHelp = useUi((s) => s.closeHelp);
  const requestTour = useUi((s) => s.requestTour);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(() => section ?? HELP_SECTIONS[0].id);

  // 마운트(=열림) 때 요청된 절로 스크롤하고 본문에 포커스 — 방향키가 씬 회전이 아니라 본문 스크롤로 간다 (OrbitKeys 는 helpOpen 에도 양보한다).
  // 닫히면(언마운트) 포커스를 열기 전 자리(헤더 ? 버튼 등)로 되돌린다 — 포커스를 가진 노드가 사라지면 body 로 떨어진다
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    const body = bodyRef.current;
    if (body) {
      const el = body.querySelector<HTMLElement>(`#help-${active}`);
      if (el) body.scrollTop = el.offsetTop - body.offsetTop;
      body.focus({ preventScroll: true });
    }
    return () => {
      if (before && document.contains(before) && typeof before.focus === "function") before.focus({ preventScroll: true });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = (id: string) => {
    setActive(id);
    const body = bodyRef.current;
    const el = body?.querySelector<HTMLElement>(`#help-${id}`);
    if (body && el) body.scrollTo({ top: el.offsetTop - body.offsetTop, behavior: "smooth" });
  };
  const show = (step: TourStepId) => {
    closeHelp();
    requestTour(step);
  };

  return (
    <aside className="help-hub card" role="dialog" aria-label={HELP_UI.button}>
      <header className="help-head">
        <span className="kick">{HELP_UI.kicker}</span>
        <b>{HELP_UI.title}</b>
        <button type="button" className="btn btn-g btn-sm" onClick={() => show("intro")} title={HELP_UI.replayTitle}>
          {HELP_UI.replay}
        </button>
        <button type="button" className="help-close" onClick={closeHelp} aria-label={HELP_UI.close} title={HELP_UI.closeTitle}>
          <Icon.Close />
        </button>
      </header>
      <div className="help-grid">
        <nav className="help-toc" aria-label={HELP_UI.tocLabel}>
          <ol>
            {HELP_SECTIONS.map((s, i) => (
              <li key={s.id}>
                <button type="button" className={active === s.id ? "on" : ""} onClick={() => go(s.id)} title={s.summary}>
                  <span className="n">{i + 1}</span>
                  {s.title}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <div className="help-body" ref={bodyRef} tabIndex={-1}>
          {HELP_SECTIONS.map((s, i) => (
            <section id={`help-${s.id}`} key={s.id}>
              <h3>
                <span className="n">{i + 1}</span>
                {s.title}
              </h3>
              <p className="meta">{s.summary}</p>
              <ul>
                {s.body.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {s.tourStep && (
                <button type="button" className="btn btn-g btn-xs" onClick={() => show(s.tourStep!)} title={HELP_UI.showOnScreenTitle}>
                  {HELP_UI.showOnScreen}
                </button>
              )}
            </section>
          ))}
        </div>
      </div>
    </aside>
  );
}
