import type { RelationshipEvidence } from "@/api/types";

/**
 * 근거 한 건을 무엇으로 보여 줄지. 함께 언급(CO_MENTIONED) 근거에는 문장이 없다 — 빅카인즈는 두 회사가 같은 문장에
 * 나왔다는 개체 분석만 주고 그 문장 원문은 주지 않는다(본문은 200자). 빈 따옴표("") 대신 기사 제목과 앞부분을 보여 준다.
 */
export const SUMMARY_PREVIEW = 120;

export type EvidenceText =
  | { kind: "sentence"; text: string }
  | { kind: "article"; title: string; preview: string | null };

type EvidenceFields = Pick<RelationshipEvidence, "evidenceSentence" | "title" | "summary">;

export function evidenceText(e: EvidenceFields): EvidenceText {
  const sentence = e.evidenceSentence?.trim();
  if (sentence) return { kind: "sentence", text: sentence };
  const summary = e.summary?.replace(/\s+/g, " ").trim() || null;
  const preview = summary && summary.length > SUMMARY_PREVIEW ? `${summary.slice(0, SUMMARY_PREVIEW)}…` : summary;
  return { kind: "article", title: e.title, preview };
}

/** 복사할 한 줄 — 문장이 있으면 따옴표로 인용하고, 없으면 기사 제목을 쓴다 */
export function evidenceCopyText(
  e: EvidenceFields & Pick<RelationshipEvidence, "publisher" | "publishedAt" | "originalUrl">,
): string {
  const t = evidenceText(e);
  const head = t.kind === "sentence" ? `"${t.text}"` : t.title;
  return `${head} — ${e.publisher ?? ""}, ${(e.publishedAt ?? "").slice(0, 10)}, ${e.originalUrl}`;
}
