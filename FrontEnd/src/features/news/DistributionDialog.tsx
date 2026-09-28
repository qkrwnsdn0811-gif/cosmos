import { useEffect } from "react";
import { Icon } from "@/components/ui";
import type { RelationshipMeta } from "@/lib/meta";

interface IndustryRow {
  name: string;
  count: number;
}
interface RelationRow {
  type: string;
  count: number;
  meta: RelationshipMeta;
}

interface Props {
  open: boolean;
  companyCount: number;
  industryDist: IndustryRow[];
  relDist: RelationRow[];
  onClose: () => void;
}

/**
 * "산업·관계 유형 분포" — 예전엔 랭킹 카드 옆에 2열로 항상 펼쳐 뒀지만, 대부분의 뉴스는
 * 언급 기업이 몇 개 안 돼 칩 한두 줄 뿐인 반면 카드 높이는 랭킹 카드에 맞춰져 있어 빈
 * 공간이 크게 남았다. 항상 보여줄 만큼 핵심적인 정보는 아니라고 보고 스크랩·원문 보기
 * 버튼 옆의 보조 버튼으로 옮기고, 눌렀을 때만 모달로 펼쳐 보여주는 것으로 바꿨다 —
 * 계산은 그대로(NewsPage 의 mapNodes/mapEdges 집계)이고 표시 위치만 바뀐다.
 */
export default function DistributionDialog({ open, companyCount, industryDist, relDist, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card dialog dist-dialog" role="dialog" aria-modal="true" aria-labelledby="dist-dialog-title">
        <div className="row between">
          <span className="kick">기업·관계 개요</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>
        <div id="dist-dialog-title" className="mt-12" style={{ fontSize: 18, fontWeight: 700 }}>
          산업·관계 유형 분포
        </div>
        <div className="meta mt-8">언급 기업 {companyCount}개를 기준으로 집계한 값입니다.</div>

        <div className="dist-col mt-16">
          <div className="lab">산업 분포</div>
          {industryDist.length ? (
            <div className="dist-chips">
              {industryDist.map((d) => (
                <span key={d.name} className="dist-chip">
                  {d.name} <b className="num">{d.count}</b>
                </span>
              ))}
            </div>
          ) : (
            <div className="meta mt-8">표시할 산업 정보가 없습니다.</div>
          )}
        </div>

        <div className="dist-col mt-16">
          <div className="lab">관계 유형 분포</div>
          {relDist.length ? (
            <div className="dist-chips">
              {relDist.map((d) => (
                <span key={d.type} className="dist-chip">
                  <i className="dot" style={{ background: d.meta.color }} /> {d.meta.label} <b className="num">{d.count}</b>
                </span>
              ))}
            </div>
          ) : (
            <div className="meta mt-8">언급된 기업들 사이의 직접적인 관계가 없습니다.</div>
          )}
        </div>
      </div>
    </div>
  );
}
