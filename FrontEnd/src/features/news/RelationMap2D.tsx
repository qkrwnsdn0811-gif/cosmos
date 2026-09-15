import type { GraphEdge, LatestGraphNode, NewsRelatedCompany } from "@/api/types";
import { RELATIONSHIP_ORDER, relationshipMeta } from "@/lib/meta";
import { CompanyAvatar, Empty, SentimentBadge } from "@/components/ui";

interface Props {
  nodes: LatestGraphNode[];
  edges: GraphEdge[];
  /** 언급 기업들 사이에 관계가 하나도 없을 때, 대신 보여줄 기업별 감성·관련도. 없어도 동작한다. */
  related?: NewsRelatedCompany[];
  highlightId?: string | null;
  onPickCompany: (id: string) => void;
}

/**
 * 뉴스 관계 지도.
 * 은하 뷰가 이미 은하 페이지에 있어 여기서 다시 그릴 이유가 없다는 팀 합의로 3D MiniScene을 걷어냈고,
 * 그다음 시도한 2열 화살표 다이어그램도 관련 기업이 수십 개로 늘어나면(공급망 전체가 엮인 뉴스 등)
 * 선이 한 지점에 뭉쳐 오히려 안 읽혔다. 노드-링크 다이어그램 자체가 "허브 하나 + 위성 다수" 구조에는
 * 근본적으로 안 맞는 형태라 판단해, 관계 유형별로 묶은 목록으로 바꿨다.
 *
 * '1홉 관계까지 확장'(뉴스가 직접 언급하지 않은 인접 기업까지 보여주는 모드)은 관련 기업이 많은
 * 뉴스에서 목록이 지나치게 길어지는 문제가 있었고, 무엇보다 '뉴스가 실제로 언급한 기업'만 보는 편이
 * 더 명확하다는 팀 합의로 기능 자체를 없앴다. 그래서 nodes/edges는 항상 "선택 뉴스가 직접 언급한
 * 기업들" 사이의 관계만 담고, 모든 엣지의 두 끝은 항상 이 기업들 중 하나다 — 그래서 더 이상
 * "직접 언급 기업 vs 그 밖의 기업"을 구분할 필요가 없어, 관계 유형별 묶음 하나로만 정리한다.
 */
export default function RelationMap2D({ nodes, edges, related, highlightId, onPickCompany }: Props) {
  if (!nodes.length) return <Empty>관계 지도를 그릴 기업이 없습니다.</Empty>;

  const byId = new Map(nodes.map((n) => [n.companyId, n]));
  const groups = RELATIONSHIP_ORDER.map((type) => ({
    type,
    meta: relationshipMeta(type),
    rows: edges
      .filter((e) => e.relationshipType === type && byId.has(e.sourceCompanyId) && byId.has(e.targetCompanyId))
      .slice()
      .sort((a, b) => b.score - a.score),
  })).filter((g) => g.rows.length > 0);

  /*
   * 언급 기업이 1개뿐인 뉴스(전체 뉴스 중 상당수)는 애초에 "관계"가 존재할 수 없다 — 비교할 상대가
   * 없기 때문이다. 이걸 그냥 빈 화면으로 두면 정작 그 기업이 어떤 감성·관련도로 언급됐는지도 사라져
   * 버리므로, 관계가 없을 땐 대신 언급 기업 카드를 보여준다. 새 데이터를 쓰지 않고 이미 받아 온
   * relatedCompanies(감성·관련도·영향도)만 재사용한다.
   */
  if (!groups.length) {
    const relatedById = new Map((related ?? []).map((r) => [r.companyId, r]));
    return (
      <div className="rel2d-wrap">
        <div className="meta">직접 언급된 기업들 사이에 뚜렷한 관계는 없어, 언급된 기업 정보만 보여줍니다.</div>
        <div className="rel2d-group mt-8">
          {nodes.map((n) => {
            const r = relatedById.get(n.companyId);
            return (
              <button key={n.companyId} type="button" className={`rank ${n.companyId === highlightId ? "on" : ""}`} onClick={() => onPickCompany(n.companyId)}>
                <CompanyAvatar name={n.name} stockCode={n.stockCode} industry={n.industryName} size={32} radius={16} />
                <span className="nm">
                  <b>{n.name}</b>
                  <span className="meta num">{[n.industryName, n.stockCode].filter(Boolean).join(" · ")}</span>
                </span>
                {r && <SentimentBadge value={r.sentiment} />}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div className="rel2d-wrap">
      {groups.map((g) => (
        <div className="rel2d-group" key={g.type}>
          <div className="rel2d-group-head">
            <i className="dot" style={{ background: g.meta.color }} />
            <span className="kick" style={{ color: g.meta.color }}>
              {g.meta.label}
            </span>
            <span className="meta num">{g.rows.length}개</span>
          </div>
          {g.rows.map((e) => {
            const a = byId.get(e.sourceCompanyId);
            const b = byId.get(e.targetCompanyId);
            if (!a || !b) return null;
            return (
              <div className="rel2d-sibling" key={e.relationshipId}>
                <button type="button" className={`rel2d-chip ${a.companyId === highlightId ? "on" : ""}`} onClick={() => onPickCompany(a.companyId)}>
                  {a.name}
                </button>
                {/* 그룹 헤더가 이미 유형을 보여주므로 행에는 점수만 남긴다 (예전엔 "협력 · 82"처럼 유형을 반복했다) */}
                <span className="meta num" style={{ color: g.meta.color, flex: "none" }}>
                  {Math.round(e.score)}
                </span>
                <button type="button" className={`rel2d-chip ${b.companyId === highlightId ? "on" : ""}`} onClick={() => onPickCompany(b.companyId)}>
                  {b.name}
                </button>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
