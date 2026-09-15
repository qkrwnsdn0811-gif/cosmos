import { useEffect } from "react";
import type { NewsDetail } from "@/api/types";
import { fmtDateTime, fmtPctFrom01 } from "@/lib/format";
import { SENTIMENT_META } from "@/lib/meta";
import { Icon, SentimentBadge, Skeleton } from "@/components/ui";

interface Props {
  open: boolean;
  news: NewsDetail | undefined;
  activeCompanyId?: string | null;
  onClose: () => void;
  onPickCompany: (companyId: string) => void;
}

/**
 * 뉴스 상세 정보를 모달로 보여준다. 이전에는 화면 맨 아래 카드에 있어 스크롤해야 확인할 수 있었는데,
 * 이제 상단의 "상세 정보" 버튼으로 바로 열 수 있다. 원문 링크도 이 모달 안에서 연다.
 */
export default function NewsDetailDialog({ open, news, activeCompanyId, onClose, onPickCompany }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card dialog news-detail" role="dialog" aria-modal="true" aria-labelledby="news-detail-title">
        <div className="row between">
          <span className="kick">news detail</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>

        {!news ? (
          <div className="mt-16">
            <Skeleton h={22} w={320} />
            <Skeleton h={16} w={220} style={{ marginTop: 10 }} />
            <Skeleton h={90} style={{ marginTop: 16 }} />
          </div>
        ) : (
          <>
            <div id="news-detail-title" className="mt-12" style={{ fontSize: 19, fontWeight: 700, lineHeight: 1.4 }}>
              {news.title}
            </div>
            <div className="row between wrap mt-8" style={{ alignItems: "center" }}>
              <span className="meta num">
                {news.publisher} · {fmtDateTime(news.publishedAt)}
                {news.author ? ` · ${news.author}` : ""}
              </span>
              <SentimentBadge value={news.relatedCompanies[0]?.sentiment ?? "NEUTRAL"} />
            </div>

            <div className="mt-12" style={{ fontSize: 14, lineHeight: 1.65, color: "var(--text-3)" }}>
              {news.summary}
            </div>

            <div className="kick mt-16">근거 문장</div>
            <div className="ev-list">
              {news.evidence.map((e, i) => (
                <div key={i} className="evidence">
                  <q>{e.sentence}</q>
                  <div className="meta num mt-8">신뢰도 {fmtPctFrom01(e.confidence)}</div>
                </div>
              ))}
            </div>

            <div className="kick mt-16">관련 기업</div>
            <div className="row wrap mt-8" style={{ gap: 8 }}>
              {news.relatedCompanies.map((r) => (
                <button
                  key={r.companyId}
                  type="button"
                  className={`chip chip-sm ${r.companyId === activeCompanyId ? "on" : ""}`}
                  onClick={() => {
                    onPickCompany(r.companyId);
                    onClose();
                  }}
                >
                  <i style={{ background: SENTIMENT_META[r.sentiment].color }} />
                  {r.name}
                  <b className="num" style={{ opacity: 0.7 }}>
                    {fmtPctFrom01(r.relevanceScore, "")}
                  </b>
                </button>
              ))}
            </div>
            <div className="meta mt-8">칩의 숫자는 관련도, 점 색은 해당 기업 기준 감성입니다. 누르면 그 기업 뉴스로 전환됩니다.</div>

            <div className="row between wrap mt-20" style={{ gap: 10 }}>
              <span className="meta">근거 문장은 프로토타입 추출 결과이며 투자 판단 자료가 아닙니다.</span>
              <a className="btn btn-p btn-sm" href={news.originalUrl} target="_blank" rel="noreferrer" style={{ flex: "none" }}>
                원문 보기 <Icon.External />
              </a>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
