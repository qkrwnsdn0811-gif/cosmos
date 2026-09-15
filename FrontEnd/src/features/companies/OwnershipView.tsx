import { useMemo, useState } from "react";
import type { LatestGraph } from "@/api/types";
import { josa } from "@/lib/format";
import { OWNERSHIP_TYPES, relationshipMeta } from "@/lib/meta";
import { CompanyAvatar, Empty, ScoreBar } from "@/components/ui";

interface Props {
  graph: LatestGraph | null;
  onPick: (companyId: string) => void;
}

/**
 * 투자·지분 관계를 지배 방향(보유 → 피보유)의 목록으로 보여준다.
 * 지주사(보유 기업)를 고르면 그 기업이 보유한 기업들과, 그 기업들이 다시 보유한 기업까지 2단계를 펼친다.
 *
 * 원래는 2차원 화살표 트리(SVG)였는데, 자회사가 많은 지주사에서 세로로 한없이 길어지고 화살표
 * 곡선이 좁은 간격에 뭉쳐 오히려 안 읽혔다 — 뉴스 페이지의 관계 지도가 겪었던 것과 같은 문제다.
 * 그래서 같은 해법(관계 점수순 스크롤 목록)을 여기에도 적용했다. 몇 단계 들어가는지 구조만 다를 뿐,
 * 클릭 동작(보유 기업이면 그 기업 기준으로 다시 펼치고, 아니면 미리보기를 연다)은 그대로다.
 */
export default function OwnershipView({ graph, onPick }: Props) {
  // 지분 관계 코드는 meta.ts 한 곳에서만 정의한다 (relationship_type 시드가 확정되면 그곳만 고친다)
  const invest = useMemo(() => graph?.edges.filter((e) => OWNERSHIP_TYPES.includes(e.relationshipType)) ?? [], [graph]);
  const info = useMemo(() => new Map(graph?.nodes.map((n) => [n.companyId, n]) ?? []), [graph]);
  const holders = useMemo(() => {
    const m = new Map<string, number>();
    invest.forEach((e) => m.set(e.sourceCompanyId, (m.get(e.sourceCompanyId) ?? 0) + 1));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).map(([id, n]) => ({ id, n, name: info.get(id)?.name ?? id }));
  }, [invest, info]);
  const holderIds = useMemo(() => new Set(holders.map((h) => h.id)), [holders]);
  const [holder, setHolder] = useState<string | null>(null);
  const root = holder ?? holders[0]?.id ?? null;

  /**
   * 다른 기업을 보유하고 있는 기업(=holders 에 있는 기업)을 누르면 그 기업 기준으로 목록을 다시 펼치고,
   * 더 이상 보유가 없는 말단 기업을 누르면 미리보기를 연다. 매번 은하로 이탈하지 않고 계열구조를 계속 탐색할 수 있게 한다.
   */
  const handleNodeClick = (id: string) => {
    if (id !== root && holderIds.has(id)) {
      setHolder(id);
      return;
    }
    onPick(id);
  };

  const tree = useMemo(() => {
    if (!root) return null;
    return invest
      .filter((e) => e.sourceCompanyId === root)
      .sort((a, b) => b.score - a.score)
      .map((e) => ({
        id: e.targetCompanyId,
        score: e.score,
        children: invest
          .filter((x) => x.sourceCompanyId === e.targetCompanyId && x.targetCompanyId !== root)
          .sort((a, b) => b.score - a.score)
          .map((x) => ({ id: x.targetCompanyId, score: x.score })),
      }));
  }, [invest, root]);

  /**
   * 목록 옆 남는 공간에 채운 요약 통계 — 이미 계산된 그래프 데이터만으로 만들어서 별도 API 호출이
   * 없다. "이 기업을 보유한 상위 기업"은 지금까지 화면에 없던 정보라 새로 추가했다: 목록은 root가
   * 보유한 쪽만 아래로 펼치기 때문에, root 자신이 다른 회사에 보유되고 있는지는 보이지 않았다.
   */
  const stats = useMemo(() => {
    if (!tree) return null;
    const level1Ids = new Set(tree.map((l1) => l1.id));
    const level2Ids = new Set(tree.flatMap((l1) => l1.children.map((c) => c.id)).filter((id) => !level1Ids.has(id)));
    const scores = tree.map((l1) => l1.score);
    const avgScore = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : 0;
    const maxEntry = tree.length ? tree.reduce((a, b) => (b.score > a.score ? b : a)) : null;
    return { directCount: level1Ids.size, totalAffiliates: level1Ids.size + level2Ids.size, avgScore, maxEntry };
  }, [tree]);
  const parentHolders = useMemo(() => (root ? invest.filter((e) => e.targetCompanyId === root).sort((a, b) => b.score - a.score) : []), [invest, root]);

  if (!graph) return <div className="own-wrap">그래프 스냅샷을 불러오는 중…</div>;
  if (!holders.length) return <Empty>스냅샷에 투자·지분 유형 관계가 없습니다.</Empty>;

  const meta = relationshipMeta(OWNERSHIP_TYPES[0]);
  const rootNode = root ? info.get(root) : undefined;

  return (
    <div className="own-wrap">
      <div className="row wrap" style={{ gap: 8 }}>
        <span className="lab" style={{ marginRight: 4 }}>
          보유 기업
        </span>
        {holders.slice(0, 14).map((h) => (
          <button key={h.id} type="button" className={`chip chip-sm ${root === h.id ? "on" : ""}`} onClick={() => setHolder(h.id)}>
            {h.name} <b className="num">{h.n}</b>
          </button>
        ))}
      </div>
      {tree && rootNode && (
        <div className="own-body">
          <div className="own-list scroll">
            <div className="own-root">
              <CompanyAvatar name={rootNode.name} stockCode={rootNode.stockCode} industry={rootNode.industryName} size={40} />
              <div>
                <b>{rootNode.name}</b>
                <span className="meta num">
                  {[rootNode.industryName, rootNode.stockCode].filter(Boolean).join(" · ")} · 보유 기업 {tree.length}개
                </span>
              </div>
            </div>
            {/*
              들여쓰기(2단계) 하나만으로는 그 의미가 "왜 이 행이 안쪽으로 밀려 있는지" 한눈에
              안 들어온다는 지적이 있었다. 그래서 (1) 1단계 목록 전체 앞에 "직접 보유" 소제목을
              달고, (2) 2단계 그룹마다 "OOO가 보유" 라벨을 따로 붙여 들여쓰기의 의미를 글로도
              명시했다. 세로선(.own-sub 의 border-left, companies.css)도 함께 그어서 그 라벨이
              가리키는 1단계 기업과 아래 2단계 행들이 한 그룹임을 시각적으로 잇는다.
            */}
            <div className="own-level-label">직접 보유</div>
            {tree.map((l1) => {
              const n = info.get(l1.id);
              if (!n) return null;
              return (
                <div key={l1.id}>
                  <OwnRow node={n} score={l1.score} color={meta.color} expandable={holderIds.has(l1.id)} onClick={() => handleNodeClick(l1.id)} />
                  {l1.children.length > 0 && (
                    <div className="own-sub">
                      <div className="own-level-label own-level-label-sub">
                        {n.name}
                        {josa(n.name, "이", "가")} 보유
                      </div>
                      {l1.children.map((l2) => {
                        const cn = info.get(l2.id);
                        if (!cn) return null;
                        return <OwnRow key={l2.id} node={cn} score={l2.score} color={meta.color} sub expandable={holderIds.has(l2.id)} onClick={() => handleNodeClick(l2.id)} />;
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* 목록 오른쪽 남는 공간 — 이미 계산돼 있던 그래프 통계를 요약 카드로 보여준다 */}
          <aside className="own-stats" aria-label="선택 기업 통계 요약">
            <div className="kick">선택 기업 요약</div>
            <div className="own-stat-row">
              <span className="lab">직접 보유 기업</span>
              <b className="num">{stats?.directCount ?? 0}개</b>
            </div>
            <div className="own-stat-row">
              <span className="lab">2단계 포함 계열사</span>
              <b className="num">{stats?.totalAffiliates ?? 0}개</b>
            </div>
            <div className="own-stat-row">
              <span className="lab">평균 지분 점수</span>
              <b className="num">{stats?.avgScore ?? 0}</b>
            </div>
            {stats?.maxEntry && (
              <div className="own-stat-row">
                <span className="lab">최고 지분 점수</span>
                <b className="num">
                  {Math.round(stats.maxEntry.score)} <span className="meta">({info.get(stats.maxEntry.id)?.name ?? "…"})</span>
                </b>
              </div>
            )}
            <div className="own-stats-divider" />
            <div className="lab">이 기업을 보유한 상위 기업</div>
            {parentHolders.length ? (
              <div className="own-parent-list">
                {parentHolders.slice(0, 4).map((e) => {
                  const holder = info.get(e.sourceCompanyId);
                  if (!holder) return null;
                  return (
                    <button key={e.relationshipId} type="button" className="own-parent-chip" onClick={() => handleNodeClick(e.sourceCompanyId)}>
                      <span className="nm">{holder.name}</span>
                      <span className="num">{Math.round(e.score)}</span>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="meta mt-8">스냅샷에서 이 기업을 보유한 상위 기업을 찾지 못했습니다.</div>
            )}
          </aside>
        </div>
      )}
      <div className="row wrap mt-12" style={{ gap: 16 }}>
        <span className="meta row" style={{ gap: 6 }}>
          <i className="dot" style={{ background: meta.color }} /> 보유 → 피보유 순서로, 관계 점수가 높은 기업부터 나열합니다
        </span>
        <span className="meta">
          <b style={{ color: "var(--text-3)" }}>▸</b> 표시가 있는 기업(다른 기업을 보유 중)을 누르면 그 기업 기준으로 계속 펼치고, 그 외 기업을 누르면 미리보기가 열립니다. 지분율은 백엔드 관계 상세에 포함되면 표시됩니다.
        </span>
      </div>
    </div>
  );
}

function OwnRow({
  node,
  score,
  color,
  sub,
  expandable,
  onClick,
}: {
  node: { companyId: string; name: string; stockCode: string | null; industryName: string | null };
  score: number;
  color: string;
  sub?: boolean;
  expandable?: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className="own-row" onClick={onClick}>
      <CompanyAvatar name={node.name} stockCode={node.stockCode} industry={node.industryName} size={sub ? 28 : 32} radius={sub ? 14 : 16} />
      <span className="nm">
        <b>{node.name}</b>
        <span className="meta num">{[node.industryName, node.stockCode].filter(Boolean).join(" · ")}</span>
      </span>
      <ScoreBar score={score} color={color} />
      <span className="sc">
        <b className="num">{Math.round(score)}</b>
      </span>
      {expandable && (
        <span className="own-arrow" aria-hidden="true">
          ▸
        </span>
      )}
    </button>
  );
}
