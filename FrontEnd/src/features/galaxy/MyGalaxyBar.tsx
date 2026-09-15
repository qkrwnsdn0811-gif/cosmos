import { useState } from "react";
import { Icon, useClickOutside } from "@/components/ui";

export interface MissingWatch {
  companyId: string;
  name: string;
}

interface Props {
  watchCount: number;
  neighborCount: number;
  includeNeighbors: boolean;
  onToggleNeighbors: () => void;
  onSearch: () => void;
  /** 우주 스냅샷 밖이라 은하에 자리가 없는 관심 기업 */
  missing: MissingWatch[];
  onPick: (companyId: string) => void;
}

/**
 * 내 은하 도구 줄 — 산업 칩 자리에 들어간다 (내 은하에서는 산업 필터를 쓰지 않는다).
 * GalaxyPage 가 이미 충분히 크므로 개인 은하 전용 HUD 는 여기로 떼어 둔다.
 */
export default function MyGalaxyBar({ watchCount, neighborCount, includeNeighbors, onToggleNeighbors, onSearch, missing, onPick }: Props) {
  const [open, setOpen] = useState(false);
  const missingRef = useClickOutside<HTMLDivElement>(() => setOpen(false), open);

  return (
    <div className="my-galaxy-bar" role="group" aria-label="내 은하 도구">
      <span className="mg-sum">
        관심 기업 <b className="num">{watchCount}</b> · 이웃 <b className="num">{neighborCount}</b>
      </span>
      <button type="button" className={`chip ${includeNeighbors ? "on" : ""}`} aria-pressed={includeNeighbors} onClick={onToggleNeighbors} title="관심 기업과 직접 이어진 기업까지 함께 올립니다">
        이웃 관계 포함
      </button>
      <button type="button" className="chip" onClick={onSearch} title="검색해서 관심 기업으로 담습니다">
        <Icon.Search /> 기업 추가
      </button>
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
            <Icon.Search /> 기업 검색 <span className="kbd">/</span>
          </button>
          <button type="button" className="btn btn-g" onClick={onBack}>
            전체 은하 보기
          </button>
        </div>
      </div>
    </div>
  );
}
