import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fmtRelative } from "@/lib/format";
import { useIndustries, useLatestComments, useLatestGraph } from "@/lib/queries";
import { CompanyAvatar, Empty, ErrorNotice, Skeleton } from "@/components/ui";
import "./community.css";

/**
 * 커뮤니티 (신설). 모든 기업에 달린 댓글을 최신순으로 한 곳에서 볼 수 있는 피드.
 * 이 페이지에서는 글쓰기를 지원하지 않는다 — 댓글 작성/수정/삭제는 기존처럼 은하 페이지의
 * 기업 인텔리전스 패널에서만 이뤄지고, 여기서는 전체 기업의 댓글을 모아 훑어보는 용도다.
 * 댓글을 누르면 그 댓글이 달린 기업을 은하 페이지에서 곧바로 확인할 수 있도록 이동한다
 * (은하 페이지가 이미 갖고 있는 기업 인텔리전스 패널 표시 방식을 그대로 재사용).
 */
export default function CommunityPage() {
  const navigate = useNavigate();
  const [industryId, setIndustryId] = useState<string | null>(null);

  const industries = useIndustries();
  // 커뮤니티 댓글 API(GlobalComment)는 산업 정보를 담고 있지 않다 — 최신 그래프 스냅샷의
  // 기업별 industryName을 프론트에서 조인해 산업 필터를 만든다. 새 백엔드 엔드포인트 없이
  // 은하 페이지가 이미 받아 쓰는 데이터를 재사용한다.
  const universe = useLatestGraph(null);
  const comments = useLatestComments(20);

  const industryOf = useMemo(() => {
    const m = new Map<string, string>();
    universe.data?.nodes.forEach((n) => {
      if (n.industryName) m.set(n.companyId, n.industryName);
    });
    return m;
  }, [universe.data]);

  const items = useMemo(() => comments.data?.pages.flatMap((p) => p.items) ?? [], [comments.data]);
  const filtered = useMemo(() => {
    if (!industryId) return items;
    return items.filter((c) => industryOf.get(c.company.companyId) === industries.data?.items.find((i) => i.industryId === industryId)?.name);
  }, [items, industryId, industryOf, industries.data]);

  const goCompany = (companyId: string) => navigate(`/?company=${companyId}`);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kick">community feed</div>
          <h1>커뮤니티</h1>
          <p>모든 기업에 달린 댓글을 최신순으로 모아 봅니다. 댓글을 누르면 해당 기업을 은하에서 바로 확인할 수 있습니다.</p>
        </div>
        <select className="industry-select" value={industryId ?? ""} onChange={(e) => setIndustryId(e.target.value || null)} aria-label="산업 필터">
          <option value="">전체 산업</option>
          {industries.data?.items
            .filter((i) => i.companyCount > 0)
            .map((i) => (
              <option key={i.industryId} value={i.industryId}>
                {i.name}
              </option>
            ))}
        </select>
      </div>

      {industryId && (
        <div className="meta comm-hint mt-8">
          {universe.isError
            ? "공개된 관계 스냅샷이 없어 산업 필터를 적용하면 모든 댓글이 숨겨집니다. 스냅샷이 게시된 뒤 다시 시도해 주세요."
            : "스냅샷에 포함되지 않은 기업의 댓글은 산업 필터에 나타나지 않을 수 있습니다."}
        </div>
      )}

      <div className="card comm-card">
        {comments.isLoading && [0, 1, 2, 3, 4].map((i) => <Skeleton key={i} h={72} style={{ marginTop: i ? 10 : 0, borderRadius: 12 }} />)}
        {!comments.isLoading && comments.isError && !filtered.length && <ErrorNotice message="댓글을 불러오지 못했습니다." onRetry={() => comments.refetch()} />}
        {!comments.isLoading && !comments.isError && !filtered.length && <Empty>표시할 댓글이 없습니다.</Empty>}
        {filtered.map((c) => (
          <button key={c.commentId} type="button" className="comm-item" onClick={() => goCompany(c.company.companyId)}>
            <CompanyAvatar name={c.company.name} companyId={c.company.companyId} stockCode={c.company.stockCode} market={c.company.market} size={36} radius={18} />
            <div className="comm-body">
              <div className="comm-company">
                <b>{c.company.name}</b>
                <span className="meta num">
                  {c.author.nickname} · {fmtRelative(c.createdAt)}
                  {c.edited ? " · 수정됨" : ""}
                </span>
              </div>
              <p className="comm-content">{c.content}</p>
            </div>
          </button>
        ))}
        {comments.hasNextPage && (
          <button type="button" className="more-btn" onClick={() => comments.fetchNextPage()} disabled={comments.isFetchingNextPage}>
            {comments.isFetchingNextPage ? "불러오는 중…" : "더 보기"}
          </button>
        )}
      </div>
    </div>
  );
}
