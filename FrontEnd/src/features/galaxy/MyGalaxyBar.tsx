import { useEffect, useRef, useState } from "react";
import { CompanyAvatar, Icon, MarketTag, useClickOutside } from "@/components/ui";
import { useCompanySearch } from "@/lib/queries";

export interface MissingWatch {
  companyId: string;
  name: string;
}

interface Props {
  watchCount: number;
  neighborCount: number;
  /** 이미 관심 등록된 기업 — 추가 검색 결과에서 중복 담기를 막는 데만 쓴다 */
  watchedIds: string[];
  /** 검색 결과를 고르면 그 기업을 관심 등록만 한다(이동 없음) — 좌측 상단 검색창(SearchPalette)과는 별개 로직 */
  onAdd: (companyId: string) => void;
  /** 우주 스냅샷 밖이라 은하에 자리가 없는 관심 기업 */
  missing: MissingWatch[];
  onPick: (companyId: string) => void;
}

/**
 * 내 은하 도구 줄 — 산업 칩 자리에 들어간다 (내 은하에서는 산업 필터를 쓰지 않는다).
 * GalaxyPage 가 이미 충분히 크므로 개인 은하 전용 HUD 는 여기로 떼어 둔다.
 * 이웃(1홉) 포함은 항상 켜져 있다 — 끄면 관심 기업끼리 어떻게 이어지는지 알 수 없어 혼란을 준다는
 * 판단으로 켜고 끄는 토글은 없앴다(store/galaxy.ts includeNeighbors 는 항상 true).
 */
export default function MyGalaxyBar({ watchCount, neighborCount, watchedIds, onAdd, missing, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const missingRef = useClickOutside<HTMLDivElement>(() => setOpen(false), open);

  return (
    <div className="my-galaxy-bar" role="group" aria-label="내 은하 도구">
      <span className="mg-sum">
        관심 기업 <b className="num">{watchCount}</b> · 이웃 <b className="num">{neighborCount}</b>
      </span>
      <AddCompanyPopover watchedIds={watchedIds} onAdd={onAdd} />
      {missing.length > 0 && (
        <div className="mg-missing" ref={missingRef}>
          <button type="button" className="chip" aria-expanded={open} onClick={() => setOpen((v) => !v)} title={`우주 스냅샷에 없어 배치하지 못한 관심 기업: ${missing.map((m) => m.name).join(", ")}`}>
            스냅샷 밖 <b className="num">{missing.length}</b>
          </button>
          {open && (
            <div className="card mg-missing-pop" role="menu">
              <div className="hint">지금 우주 스냅샷에 없어 은하에 자리가 없습니다. 누르면 그 기업 관계망으로 들어갑니다.</div>
              {missing.map((m) => (
                <button key={m.companyId} type="button" role="menuitem" onClick={() => (setOpen(false), onPick(m.companyId))}>
                  {m.name}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * '기업 추가' — 좌측 상단 검색창(SearchPalette)과는 완전히 별도의 검색 로직이다.
 * 관계 검색 없이 기업명(·종목코드) 검색만 있고, 결과를 고르면 그 기업의 은하로 이동하지 않고 관심 등록만 한다 —
 * 관심 목록(useWatchlist)이 그 즉시 다시 불러와지면서(useToggleWatch 의 invalidateQueries) 내 은하가 저절로 새로고침된다.
 * 버튼 아래 붙는 작은 팝오버로, 고를 때마다 검색어만 비우고 열려 있어 여러 기업을 이어서 담을 수 있다.
 */
function AddCompanyPopover({ watchedIds, onAdd }: { watchedIds: string[]; onAdd: (companyId: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const popRef = useClickOutside<HTMLDivElement>(() => setOpen(false), open);
  const results = useCompanySearch(q, open);
  const items = results.data?.items ?? [];

  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => input.current?.focus(), 10);
    return () => clearTimeout(t);
  }, [open]);

  const pick = (companyId: string) => {
    if (watchedIds.includes(companyId)) return;
    onAdd(companyId);
    setQ("");
    input.current?.focus();
  };

  return (
    <div className="mg-add" ref={popRef}>
      <button type="button" className="chip" aria-expanded={open} onClick={() => setOpen((v) => !v)} title="검색해서 관심 기업으로 담습니다(은하 이동 없이 바로 추가)">
        <Icon.Search /> 기업 추가
      </button>
      {open && (
        <div className="card mg-add-pop" role="dialog" aria-label="관심 기업 추가">
          <label className="field">
            <Icon.Search />
            <input
              ref={input}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="기업명 또는 종목코드를 검색하세요"
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
                else if (e.key === "Enter" && items[0]) pick(items[0].companyId);
              }}
            />
          </label>
          {q.trim() ? (
            items.length ? (
              <ul className="scroll mg-add-list">
                {items.map((c) => {
                  const already = watchedIds.includes(c.companyId);
                  return (
                    <li key={c.companyId}>
                      <button type="button" disabled={already} onClick={() => pick(c.companyId)}>
                        <CompanyAvatar name={c.name} stockCode={c.stockCode} market={c.market} industry={c.primaryIndustry?.name} size={30} />
                        <span className="grow">
                          <span className="nm">{c.name}</span>
                          <div className="meta num" style={{ marginTop: 2 }}>
                            {c.stockCode ? `${c.stockCode} · ` : ""}
                            <MarketTag market={c.market} />
                          </div>
                        </span>
                        {already && <span className="meta">담김</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <div className="hint">{results.isFetching ? "검색 중…" : "일치하는 기업이 없습니다."}</div>
            )
          ) : (
            <div className="hint">검색해서 고르면 은하 이동 없이 바로 관심 기업으로 담기고, 내 은하가 새로고침됩니다.</div>
          )}
        </div>
      )}
    </div>
  );
}

/** 관심 기업이 하나도 없거나(별 0개), 있어도 전부 우주 스냅샷 밖이라 배치할 자리가 없을 때 무대 가운데에 뜨는 안내
    — 빈 씬만 보여 주면 고장으로 읽힌다 */
export function MyGalaxyEmpty({ outOfSnapshot, onSearch, onBack }: { outOfSnapshot?: boolean; onSearch: () => void; onBack: () => void }) {
  return (
    <div className="mg-empty">
      <div className="card mg-empty-card">
        <span className="kick">my galaxy</span>
        {outOfSnapshot ? (
          <>
            <h2>관심 기업이 지금 우주 스냅샷 밖에 있습니다</h2>
            <p className="hint">우주 스냅샷은 상위 기업 일부만 돌아가며 담습니다. 관심 기업이 스냅샷에 들어오면 다시 은하가 그려집니다.</p>
          </>
        ) : (
          <>
            <h2>아직 내 은하에 별이 없습니다</h2>
            <p className="hint">은하에서 기업을 고르고 ★ 관심 등록을 누르면 이곳에 개인 은하가 만들어집니다.</p>
          </>
        )}
        <div className="row mt-16">
          <button type="button" className="btn btn-p" onClick={onSearch}>
            <Icon.Search /> 기업 검색
          </button>
          <button type="button" className="btn btn-g" onClick={onBack}>
            전체 은하 보기
          </button>
        </div>
      </div>
    </div>
  );
}
