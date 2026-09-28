import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCompanyGraph, useCompanySearch } from "@/lib/queries";
import { useGalaxy } from "@/store/galaxy";
import { isTyping, modalOpen } from "@/three/keyboard";
import { CompanyAvatar, Icon, IndustryBadge, MarketTag } from "@/components/ui";

interface Props {
  onPick: (companyId: string) => void;
}

/** Ctrl+K 기업 검색. 기업명·영문명·종목코드로 찾는다 (GET /api/companies?keyword=). */
export default function SearchPalette({ onPick }: Props) {
  const open = useGalaxy((s) => s.searchOpen);
  const setOpen = useGalaxy((s) => s.setSearchOpen);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!useGalaxy.getState().searchOpen);
      } else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !useGalaxy.getState().searchOpen && !isTyping() && !modalOpen()) {
        // 화면에 안내하는 단축키. 입력 상자에 '/' 를 치는 중이거나 다른 모달이 떠 있으면 그대로 둔다 (Ctrl/Cmd+K 는 숨은 별칭)
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "Escape" && useGalaxy.getState().searchOpen) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  // 열 때마다 검색어와 선택 위치를 새로 초기화한다.
  return open ? <SearchPaletteDialog onPick={onPick} onClose={() => setOpen(false)} /> : null;
}

type SearchMode = "company" | "relation";

/** 관계 검색 1단계에서 고른 기업 — 2단계 검색으로 넘어가면 이 값과 짝지어 경로를 찾는다 */
interface RelPick {
  companyId: string;
  name: string;
}

function SearchPaletteDialog({ onPick, onClose }: Props & { onClose: () => void }) {
  const navigate = useNavigate();
  const [mode, setMode] = useState<SearchMode>("company");
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  /** 관계 검색의 첫 번째 기업 — 아직 없으면 지금 검색은 "첫 기업" 을 찾는 중이다 */
  const [relFrom, setRelFrom] = useState<RelPick | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const results = useCompanySearch(q);
  const items = results.data?.items ?? [];
  // ControlDock 의 관계선 기준 기간(전역) — 여기서 추천하는 관계도 같은 기준으로 다시 계산된 간선을 써야,
  // 추천을 골라 넘어가는 관계 경로 화면(PathGalaxyPage)의 관계선과 어긋나지 않는다
  const edgeWindow = useGalaxy((s) => s.edgeWindow);

  /**
   * 관계 검색 1단계에서 기업을 고르면, 그 기업과 관계 점수가 가장 높은 기업들을 연관 검색어처럼 바로 보여준다 —
   * 두 번째 기업명을 몰라도 여기서 바로 골라 경로 검색으로 이어갈 수 있다. 기업 중심 그래프(최대 3단계) 중
   * relFrom 에 직접 붙은 간선(depth 1)만 추려 점수 내림차순 상위 몇 개만 남긴다.
   */
  const relGraph = useCompanyGraph(mode === "relation" && relFrom ? relFrom.companyId : null, edgeWindow);
  const relSuggestions = useMemo(() => {
    if (!relGraph.data) return [];
    const { edges, centerCompanyId, nodes } = relGraph.data;
    const nameById = new Map(nodes.map((n) => [n.companyId, n.name]));
    const bestScore = new Map<string, number>();
    edges.forEach((e) => {
      const otherId = e.sourceCompanyId === centerCompanyId ? e.targetCompanyId : e.targetCompanyId === centerCompanyId ? e.sourceCompanyId : null;
      if (!otherId) return;
      const cur = bestScore.get(otherId) ?? -1;
      if (e.score > cur) bestScore.set(otherId, e.score);
    });
    return [...bestScore.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([companyId, score]) => ({ companyId, name: nameById.get(companyId) ?? companyId, score }));
  }, [relGraph.data]);

  useEffect(() => {
    const timer = setTimeout(() => input.current?.focus(), 10);
    return () => clearTimeout(timer);
    // 관계 검색에서 첫 기업을 고르고 두 번째 칸으로 넘어갈 때도 다시 포커스한다
  }, [mode, relFrom]);

  const pick = (id: string) => {
    onClose();
    onPick(id);
  };

  const switchMode = (m: SearchMode) => {
    if (m === mode) return;
    setMode(m);
    setQ("");
    setCursor(0);
    setRelFrom(null);
  };

  /** 관계 검색에서 기업을 하나 고른다 — 첫 번째면 두 번째 칸을 열고, 두 번째면 두 기업을 잇는 경로 은하로 이동한다 */
  const pickRelation = (c: RelPick) => {
    if (!relFrom) {
      setRelFrom(c);
      setQ("");
      setCursor(0);
      return;
    }
    if (c.companyId === relFrom.companyId) return; // 같은 기업이면 경로가 없다 — 무시
    onClose();
    navigate(`/path?from=${relFrom.companyId}&to=${c.companyId}`);
  };

  const onItemPick = (c: { companyId: string; name: string }) => (mode === "company" ? pick(c.companyId) : pickRelation(c));

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card palette" role="dialog" aria-modal="true" aria-label={mode === "company" ? "기업 검색" : "관계 검색"}>
        {/* 기업명 검색(기본) ⟷ 관계 검색 — 두 기업을 이어 주는 경로를 찾는 모드로 전환한다 */}
        <div className="seg palette-mode" role="group" aria-label="검색 방식">
          <button type="button" className={mode === "company" ? "on" : ""} onClick={() => switchMode("company")}>
            기업명 검색
          </button>
          <button type="button" className={mode === "relation" ? "on" : ""} onClick={() => switchMode("relation")}>
            관계 검색
          </button>
        </div>
        {mode === "relation" && relFrom && (
          <div className="palette-relation-step">
            <span className="chip on">{relFrom.name}</span>
            <span aria-hidden="true">→</span>
            <span className="meta">두 번째 기업을 검색하세요</span>
            <button type="button" className="btn btn-g btn-sm" onClick={() => setRelFrom(null)}>
              다시 선택
            </button>
          </div>
        )}
        <label className="field">
          <Icon.Search />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setCursor(0);
            }}
            placeholder={mode === "company" ? "기업명 또는 종목코드를 검색하세요" : relFrom ? `${relFrom.name} 와(과) 이어질 기업을 검색하세요` : "관계를 찾을 첫 번째 기업을 검색하세요"}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(items.length - 1, c + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(0, c - 1));
              } else if (e.key === "Enter" && items[cursor]) onItemPick(items[cursor]);
            }}
          />
          <span className="kbd">ESC</span>
        </label>
        {mode === "relation" && relFrom && (
          <div className="palette-suggest">
            <span className="meta">{relFrom.name}와(과) 관계 점수가 가장 높은 기업</span>
            {relGraph.isLoading ? (
              <span className="meta">불러오는 중…</span>
            ) : relSuggestions.length ? (
              <div className="palette-suggest-chips">
                {relSuggestions.map((s) => (
                  <button key={s.companyId} type="button" className="chip" onClick={() => pickRelation(s)}>
                    {s.name}
                    <b className="num">{Math.round(s.score)}</b>
                  </button>
                ))}
              </div>
            ) : (
              <span className="meta">연결된 기업이 없습니다.</span>
            )}
          </div>
        )}
        {q.trim() ? (
          items.length ? (
            <ul className="scroll">
              {items.map((c, i) => (
                <li key={c.companyId} className={i === cursor ? "on" : ""}>
                  <button type="button" onMouseEnter={() => setCursor(i)} onClick={() => onItemPick(c)}>
                    <CompanyAvatar name={c.name} stockCode={c.stockCode} market={c.market} industry={c.primaryIndustry?.name} size={36} />
                    <span className="grow">
                      <span className="nm">{c.name}</span>
                      <span className="meta" style={{ marginLeft: 8 }}>
                        {c.nameEn}
                      </span>
                      <div className="meta num" style={{ marginTop: 2 }}>
                        {c.stockCode ? `${c.stockCode} · ` : ""}
                        <MarketTag market={c.market} />
                      </div>
                    </span>
                    <IndustryBadge name={c.primaryIndustry?.name} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">{results.isFetching ? "검색 중…" : "일치하는 기업이 없습니다."}</div>
          )
        ) : (
          <div className="empty">
            {mode === "company" ? (
              <>
                삼성, hynix, 005930 처럼 입력하세요. <span className="kbd">↑</span> <span className="kbd">↓</span> 로 이동, <span className="kbd">Enter</span> 로 해당 기업 은하로 워프합니다.
              </>
            ) : relFrom ? (
              <>
                두 번째 기업명을 입력하고 <span className="kbd">Enter</span> 를 누르면 두 기업을 잇는 관계 경로로 이동합니다.
              </>
            ) : (
              <>
                관계를 확인할 첫 번째 기업명을 입력하세요. 이어서 두 번째 기업을 검색해 <span className="kbd">Enter</span> 를 누르면, 두 기업 사이를 잇는 관계 경로만 모은 은하로 이동합니다.
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
