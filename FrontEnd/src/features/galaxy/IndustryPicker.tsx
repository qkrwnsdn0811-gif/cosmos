import { useEffect, useMemo } from "react";
import type { Industry } from "@/api/types";
import { INDUSTRY_GROUP_ORDER, industryColor, industryGroup } from "@/lib/meta";
import { useGalaxy } from "@/store/galaxy";
import { Icon, useClickOutside } from "@/components/ui";

interface Props {
  industries: Industry[];
  /** 전체 우주 기업 수 — "전체" 항목의 숫자 */
  total: number | undefined;
  value: string | null;
  onChange(industryId: string | null): void;
}

interface Section {
  title: string;
  items: Industry[];
}

/**
 * 산업을 묶어 섹션으로 나눈다. 서버가 parentIndustryId 를 채워 주면 그 계층(부모 이름 = 섹션)을 쓰고,
 * 아직 평면(목업)이면 lib/meta 의 임시 분류(industryGroup)로 묶는다. 섹션 안은 기업 수 내림차순.
 */
function groupIndustries(all: Industry[], visible: Industry[]): Section[] {
  const nameOf = new Map(all.map((i) => [i.industryId, i.name]));
  const useParents = visible.some((i) => i.parentIndustryId !== null && nameOf.has(i.parentIndustryId));
  const buckets = new Map<string, Industry[]>();
  visible.forEach((i) => {
    // 계층이 있을 때 부모가 없는 산업(최상위)은 자기 이름의 섹션에 스스로 들어간다 — 부모에 직접 속한 기업도 고를 수 있게
    const title = useParents ? (i.parentIndustryId ? nameOf.get(i.parentIndustryId) ?? "기타" : i.name) : industryGroup(i.name);
    if (!buckets.has(title)) buckets.set(title, []);
    buckets.get(title)!.push(i);
  });
  const rank = (t: string) => {
    const k = INDUSTRY_GROUP_ORDER.indexOf(t);
    return k < 0 ? 99 : k;
  };
  return [...buckets.entries()]
    .sort((a, b) => rank(a[0]) - rank(b[0]) || b[1].length - a[1].length || a[0].localeCompare(b[0]))
    .map(([title, items]) => ({ title, items: items.slice().sort((a, b) => b.companyCount - a.companyCount || a.name.localeCompare(b.name)) }));
}

/**
 * 상단 산업 필터 — 19개 칩이 세 줄로 씬을 덮던 자리를 요약 칩 하나로 접고, 누르면 묶음별 선택판이 열린다.
 * 접혀 있어도 현재 필터(산업명·기업 수·색)는 요약 칩에 남는다. 단일 선택이라 고르면 바로 닫히고, 바깥 클릭·ESC(GalaxyPage)로도 닫힌다.
 */
export default function IndustryPicker({ industries, total, value, onChange }: Props) {
  const open = useGalaxy((s) => s.industryOpen);
  const setOpen = useGalaxy((s) => s.setIndustryOpen);
  const ref = useClickOutside<HTMLDivElement>(() => setOpen(false), open);
  const visible = useMemo(() => industries.filter((i) => i.companyCount > 0), [industries]);
  const sections = useMemo(() => groupIndustries(industries, visible), [industries, visible]);
  const current = value ? industries.find((i) => i.industryId === value) : undefined;
  // 산업 목록이 다시 내려와 선택한 산업이 사라졌으면(분류 개편 등) 필터를 푼다 — 요약 칩은 "전체" 인데 그래프만 걸러진 채 남지 않게.
  // 목록이 비어 있는 동안(로딩)은 판단을 미룬다
  useEffect(() => {
    if (value && industries.length && !current) onChange(null);
  }, [value, industries, current, onChange]);
  const pick = (id: string | null) => {
    onChange(id);
    setOpen(false);
  };

  return (
    <div className="industry-picker" ref={ref}>
      <button
        type="button"
        className={`field industry-trigger ${current ? "on" : ""}`}
        onClick={() => setOpen(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={current ? `산업 필터: ${current.name} — 누르면 바꿉니다` : "산업으로 은하를 걸러 봅니다"}
      >
        <i style={{ background: current ? industryColor(current.name) : "var(--text-4)" }} />
        <span className="lab">산업</span>
        <b>{current ? current.name : "전체"}</b>
        <span className="num">{current ? current.companyCount : total ?? "-"}</span>
        <Icon.Chevron />
      </button>
      {open && (
        <div className="card industry-pop" role="listbox" aria-label="산업 필터">
          <div className="row between industry-pop-head">
            <span className="kick">산업 필터</span>
            <button type="button" role="option" aria-selected={value === null} className={`chip chip-sm ${value === null ? "on" : ""}`} onClick={() => pick(null)}>
              전체 보기 <b className="num">{total ?? "-"}</b>
            </button>
          </div>
          {sections.map((sec) => (
            <div key={sec.title} className="industry-sec">
              <div className="lab">{sec.title}</div>
              <div className="industry-sec-items">
                {sec.items.map((i) => (
                  <button
                    key={i.industryId}
                    type="button"
                    role="option"
                    aria-selected={value === i.industryId}
                    className={`chip ${value === i.industryId ? "on" : ""}`}
                    onClick={() => pick(value === i.industryId ? null : i.industryId)}
                  >
                    <i style={{ background: industryColor(i.name) }} />
                    {i.name} <b className="num">{i.companyCount}</b>
                  </button>
                ))}
              </div>
            </div>
          ))}
          {!sections.length && <div className="hint">산업 정보를 불러오는 중입니다.</div>}
        </div>
      )}
    </div>
  );
}
