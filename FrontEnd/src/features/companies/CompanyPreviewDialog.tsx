import { useEffect } from "react";
import { fmtCompactPrice, fmtDateTime, fmtNumber } from "@/lib/format";
import { marketCurrency } from "@/lib/meta";
import { useCompany, useCompanyMetrics, useToggleWatch } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { CompanyAvatar, Icon, IndustryBadge, Kpi, MarketTag, Skeleton } from "@/components/ui";

interface Props {
  companyId: string | null;
  onClose: () => void;
  onOpenGalaxy: (companyId: string) => void;
}

/**
 * 기업 디렉터리 · 관심 기업 · 투자·지분 트리에서 기업을 눌렀을 때 여는 가벼운 미리보기.
 * 지금까지는 클릭하면 곧바로 전체 은하로 워프해야만 기본 정보를 볼 수 있었는데,
 * 시세·산업·최근 지표 정도의 요약은 이 모달로 바로 보여주고, 관계망 탐색이 필요할 때만
 * "은하에서 관계 보기"로 넘어가도록 분리한다.
 */
export default function CompanyPreviewDialog({ companyId, onClose, onOpenGalaxy }: Props) {
  const authed = useSession((s) => s.status === "authed");
  const openAuth = useUi((s) => s.openAuth);
  const toast = useUi((s) => s.toast);
  const company = useCompany(companyId);
  const metrics = useCompanyMetrics(companyId, "30D");
  const toggleWatch = useToggleWatch();

  useEffect(() => {
    if (!companyId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [companyId, onClose]);

  if (!companyId) return null;
  const c = company.data;

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card dialog company-preview" role="dialog" aria-modal="true" aria-labelledby="company-preview-title">
        <div className="row between">
          <span className="kick">company preview</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>

        {!c ? (
          <div className="mt-16">
            <Skeleton h={48} w={48} style={{ borderRadius: 12 }} />
            <Skeleton h={20} w={160} style={{ marginTop: 14 }} />
            <Skeleton h={14} w={220} style={{ marginTop: 8 }} />
          </div>
        ) : (
          <>
            <div className="row mt-12" style={{ gap: 14, alignItems: "flex-start" }}>
              <CompanyAvatar name={c.name} stockCode={c.stockCode} industry={c.industries[0]?.name} size={48} />
              <div className="grow">
                <div id="company-preview-title" style={{ fontSize: 19, fontWeight: 700 }}>
                  {c.name}
                </div>
                <div className="meta num mt-8">
                  {c.nameEn} · {c.stockCode} · <MarketTag market={c.market} />
                </div>
                <div className="row wrap mt-8" style={{ gap: 6 }}>
                  {c.industries.map((i) => (
                    <IndustryBadge key={i.industryId} name={i.name} />
                  ))}
                </div>
              </div>
              <button
                type="button"
                className={`btn btn-g btn-sm ${c.watched ? "on" : ""}`}
                style={{ color: c.watched ? "var(--primary-2)" : undefined, flex: "none" }}
                onClick={() =>
                  authed
                    ? toggleWatch.mutate(
                        { companyId: c.companyId, watched: c.watched },
                        { onSuccess: (now) => toast(now ? "관심 기업으로 등록했습니다." : "관심 기업에서 해제했습니다.", "success") },
                      )
                    : openAuth("login")
                }
              >
                <Icon.Star filled={c.watched} /> {c.watched ? "관심" : "관심 +"}
              </button>
            </div>

            {c.description && (
              <p className="meta mt-12" style={{ lineHeight: 1.6 }}>
                {c.description}
              </p>
            )}

            <div className="row wrap mt-16" style={{ gap: 10 }}>
              <Kpi label="뉴스 언급 (30일)" value={metrics.data ? fmtNumber(metrics.data.newsMentionCount) : "-"} unit="건" />
              <Kpi label="뉴스 분위기" value={metrics.data?.sentimentScore != null ? Math.round(metrics.data.sentimentScore * 100) : "-"} unit="/100" />
              <Kpi label="연결 관계" value={metrics.data ? fmtNumber(metrics.data.relationshipCount) : "-"} unit="개" />
              {/* 시가총액은 company 테이블에 아직 컬럼이 없어 백엔드가 값을 줄 때만 나타난다(은하 뷰 주가 탭과 동일 규칙) */}
              {c.marketCap != null && <Kpi label="시가총액" value={fmtCompactPrice(c.marketCap, marketCurrency(c.market))} />}
            </div>
            {metrics.data?.measuredAt && <div className="meta mt-8">{fmtDateTime(metrics.data.measuredAt)} 기준 지표</div>}

            <div className="row between wrap mt-20" style={{ gap: 10 }}>
              <span className="meta">더 자세한 관계망 · 주가 · 게시판은 은하 뷰의 기업 패널에서 볼 수 있습니다.</span>
              <button type="button" className="btn btn-p btn-sm" style={{ flex: "none" }} onClick={() => onOpenGalaxy(c.companyId)}>
                은하에서 관계 보기 <Icon.Galaxy />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
