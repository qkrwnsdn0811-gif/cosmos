package com.cosmos.api.event.dto;

import java.math.BigDecimal;
import java.time.Instant;

/** 관계 근거 조각: 빅카인즈 검색어 주변 구간(300자 이하)과 원문 링크. 본문 전체는 저장하지 않는다. */
public record EvidenceSnippetResponse(
        String bigkindsNewsId,
        String title,
        String publisher,
        Instant publishedAt,
        String originalUrl,
        String snippet,
        BigDecimal confidence
) {
}
