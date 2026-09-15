import { useEffect, useRef, useState } from "react";
import { useCompanySearch } from "@/lib/queries";
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

function SearchPaletteDialog({ onPick, onClose }: Props & { onClose: () => void }) {
  const [q, setQ] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const results = useCompanySearch(q);
  const items = results.data?.items ?? [];

  useEffect(() => {
    const timer = setTimeout(() => input.current?.focus(), 10);
    return () => clearTimeout(timer);
  }, []);

  const pick = (id: string) => {
    onClose();
    onPick(id);
  };

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card palette" role="dialog" aria-modal="true" aria-label="기업 검색">
        <label className="field">
          <Icon.Search />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setCursor(0);
            }}
            placeholder="기업명 또는 종목코드를 검색하세요"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(items.length - 1, c + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(0, c - 1));
              } else if (e.key === "Enter" && items[cursor]) pick(items[cursor].companyId);
            }}
          />
          <span className="kbd">ESC</span>
        </label>
        {q.trim() ? (
          items.length ? (
            <ul className="scroll">
              {items.map((c, i) => (
                <li key={c.companyId} className={i === cursor ? "on" : ""}>
                  <button type="button" onMouseEnter={() => setCursor(i)} onClick={() => pick(c.companyId)}>
                    <CompanyAvatar name={c.name} stockCode={c.stockCode} industry={c.primaryIndustry.name} size={36} />
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
                    <IndustryBadge name={c.primaryIndustry.name} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="empty">{results.isFetching ? "검색 중…" : "일치하는 기업이 없습니다."}</div>
          )
        ) : (
          <div className="empty">
            삼성, hynix, 005930 처럼 입력하세요. <span className="kbd">↑</span> <span className="kbd">↓</span> 로 이동, <span className="kbd">Enter</span> 로 해당 기업 은하로 워프합니다.
          </div>
        )}
      </div>
    </div>
  );
}
