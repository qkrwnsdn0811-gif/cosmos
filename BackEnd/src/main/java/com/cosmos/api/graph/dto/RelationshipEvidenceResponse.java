package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/**
 * 관계 근거 한 건.
 *
 * <p>뉴스 기사와 공시를 같이 담는다. {@code documentType} 이 NEWS 면 {@code evidenceSentence}
 * 는 기사 원문에서 그대로 오려낸 문장이고, DISCLOSURE 면 공시 내용을 요약해 만든 문장이다.
 * 어느 쪽인지는 {@code modelVersion} 으로도 구분된다. 화면에서 둘을 같은 자리에 섞으면
 * 사용자가 인용문과 생성문을 구분하지 못하므로, 표시를 나눠야 한다.
 *
 * <p>공시는 {@code publishedAt} 이 비어 있어 접수일(filing_date)을 대신 쓴다.
 */
public record RelationshipEvidenceResponse(
        UUID newsId,
        String title,
        String summary,
        String publisher,
        String originalUrl,
        Instant publishedAt,
        String documentType,
        String evidenceSentence,
        String modelVersion,
        BigDecimal contributionScore,
        BigDecimal confidence
) {
}
