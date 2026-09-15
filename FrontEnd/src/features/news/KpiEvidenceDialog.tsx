import { useEffect } from "react";
import type { NewsDetail, Sentiment } from "@/api/types";
import { fmtPctFrom01 } from "@/lib/format";
import { SENTIMENT_META } from "@/lib/meta";
import { CompanyAvatar, Empty, Icon, SentimentBadge } from "@/components/ui";

export type KpiEvidenceKind = "companies" | "sentiment" | "evidence" | "confidence";

interface Props {
  kind: KpiEvidenceKind | null;
  news: NewsDetail | undefined;
  onClose: () => void;
  onPickCompany: (companyId: string) => void;
}

const TITLE: Record<KpiEvidenceKind, string> = {
  companies: "선택 뉴스 연결 기업",
  sentiment: "긍정 / 부정 기업",
  evidence: "대표 근거 문장",
  confidence: "분석 신뢰도",
};

/**
 * 뉴스 상단 KPI 4종(연결 기업 · 긍정/부정 기업 · 근거 문장 · 분석 신뢰도)을 누르면 그 수치가
 * 정확히 어떤 데이터로 계산됐는지 보여준다. 네 수치 모두 NewsPage 의 `kpi` 계산이
 * detail.relatedCompanies / detail.evidence 를 그대로 세거나 평균 낸 값이므로, 이 모달도 같은
 * 두 배열만 다르게 잘라 보여준다 — 화면에 없던 근거를 새로 만들지 않는다.
 */
export default function KpiEvidenceDialog({ kind, news, onClose, onPickCompany }: Props) {
  useEffect(() => {
    if (!kind) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [kind, onClose]);

  if (!kind || !news) return null;

  const pick = (companyId: string) => {
    onPickCompany(companyId);
    onClose();
  };

  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <div className="card dialog kpi-evidence" role="dialog" aria-modal="true" aria-labelledby="kpi-evidence-title">
        <div className="row between">
          <span className="kick">근거</span>
          <button type="button" className="btn btn-g btn-xs" onClick={onClose} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>
        <div id="kpi-evidence-title" className="mt-12" style={{ fontSize: 18, fontWeight: 700 }}>
          {TITLE[kind]}
        </div>

        {kind === "companies" && <CompaniesBasis news={news} onPick={pick} />}
        {kind === "sentiment" && <SentimentBasis news={news} onPick={pick} />}
        {kind === "evidence" && <EvidenceBasis news={news} />}
        {kind === "confidence" && <ConfidenceBasis news={news} />}

        <div className="meta mt-16">
          이 뉴스에서 추출된 관련 기업·근거 문장을 그대로 집계한 값이며, 프로토타입 계산식으로 투자 판단 자료가 아닙니다.
        </div>
      </div>
    </div>
  );
}

function CompaniesBasis({ news, onPick }: { news: NewsDetail; onPick: (id: string) => void }) {
  if (!news.relatedCompanies.length) return <Empty>이 뉴스에 연결된 기업이 없습니다.</Empty>;
  return (
    <>
      <div className="meta mt-8">
        이 뉴스가 언급한 기업 <b className="num">{news.relatedCompanies.length}</b>개를 센 값입니다. 기업을 누르면 그 기업 뉴스로 전환됩니다.
      </div>
      <div className="ev-list mt-12">
        {news.relatedCompanies.map((r) => (
          <button key={r.companyId} type="button" className="rank" onClick={() => onPick(r.companyId)}>
            <CompanyAvatar name={r.name} size={32} radius={16} />
            <span className="nm">
              <b>{r.name}</b>
              <span className="meta num">
                관련도 {fmtPctFrom01(r.relevanceScore)} · 영향도 {fmtPctFrom01(r.impactScore)}
              </span>
            </span>
            <SentimentBadge value={r.sentiment} />
          </button>
        ))}
      </div>
    </>
  );
}

const SENTIMENT_ORDER: Sentiment[] = ["POSITIVE", "NEGATIVE", "NEUTRAL"];

function SentimentBasis({ news, onPick }: { news: NewsDetail; onPick: (id: string) => void }) {
  const groups = SENTIMENT_ORDER.map((s) => ({ sentiment: s, rows: news.relatedCompanies.filter((r) => r.sentiment === s) }));
  const [pos, neg, neu] = groups.map((g) => g.rows.length);
  const shown = groups.filter((g) => g.rows.length > 0);
  if (!shown.length) return <Empty>이 뉴스에 연결된 기업이 없습니다.</Empty>;
  return (
    <>
      <div className="meta mt-8">
        연결 기업 각각에 매겨진 감성 태그를 센 값입니다 — 긍정 <b className="num">{pos}</b> · 부정 <b className="num">{neg}</b> · 중립{" "}
        <b className="num">{neu}</b>.
      </div>
      {shown.map((g) => (
        <div key={g.sentiment} className="mt-12">
          <div className="row" style={{ gap: 7, alignItems: "center" }}>
            <i className="dot" style={{ background: SENTIMENT_META[g.sentiment].color }} />
            <span className="kick" style={{ color: SENTIMENT_META[g.sentiment].color }}>
              {SENTIMENT_META[g.sentiment].label} · {g.rows.length}개
            </span>
          </div>
          <div className="row wrap mt-8" style={{ gap: 8 }}>
            {g.rows.map((r) => (
              <button key={r.companyId} type="button" className="chip chip-sm" onClick={() => onPick(r.companyId)}>
                {r.name}
                <b className="num" style={{ opacity: 0.7 }}>
                  {fmtPctFrom01(r.relevanceScore, "")}
                </b>
              </button>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function EvidenceBasis({ news }: { news: NewsDetail }) {
  if (!news.evidence.length) return <Empty>추출된 근거 문장이 없습니다.</Empty>;
  return (
    <>
      <div className="meta mt-8">
        이 뉴스에서 추출된 근거 문장 <b className="num">{news.evidence.length}</b>개입니다.
      </div>
      <div className="ev-list mt-12">
        {news.evidence.map((e, i) => (
          <div key={i} className="evidence">
            <q>{e.sentence}</q>
            <div className="meta num mt-8">신뢰도 {fmtPctFrom01(e.confidence)}</div>
          </div>
        ))}
      </div>
    </>
  );
}

function ConfidenceBasis({ news }: { news: NewsDetail }) {
  if (!news.evidence.length) return <Empty>추출된 근거 문장이 없어 신뢰도를 계산할 수 없습니다.</Empty>;
  const parts = news.evidence.map((e) => e.confidence ?? 0);
  const avg = Math.round((parts.reduce((s, v) => s + v, 0) / parts.length) * 100);
  const hasMissing = news.evidence.some((e) => e.confidence == null);
  return (
    <>
      <div className="meta mt-8">
        근거 문장 {news.evidence.length}개의 신뢰도 평균입니다{hasMissing ? " (신뢰도가 없는 문장은 0으로 계산합니다)" : ""}.
      </div>
      <div className="ev-list mt-12">
        {news.evidence.map((e, i) => (
          <div key={i} className="evidence">
            <q>{e.sentence}</q>
            <div className="meta num mt-8">신뢰도 {fmtPctFrom01(e.confidence)}</div>
          </div>
        ))}
      </div>
      <div className="row between mt-12" style={{ padding: "10px 0", borderTop: "1px solid rgba(255,255,255,0.08)" }}>
        <span className="meta">
          평균 = ({parts.map((p) => Math.round(p * 100)).join(" + ")}) / {parts.length}
        </span>
        <b className="num" style={{ fontSize: 18 }}>
          {avg}/100
        </b>
      </div>
    </>
  );
}
