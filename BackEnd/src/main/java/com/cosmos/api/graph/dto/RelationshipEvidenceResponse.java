package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

public record RelationshipEvidenceResponse(
        UUID newsId,
        String title,
        String summary,
        String publisher,
        String originalUrl,
        Instant publishedAt,
        String evidenceSentence,
        BigDecimal contributionScore,
        BigDecimal confidence
) {
}
