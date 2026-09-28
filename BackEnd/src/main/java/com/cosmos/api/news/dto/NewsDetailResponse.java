package com.cosmos.api.news.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record NewsDetailResponse(
        UUID newsId,
        String title,
        String summary,
        String publisher,
        String author,
        String originalUrl,
        Instant publishedAt,
        List<NewsRelatedCompanyResponse> relatedCompanies,
        List<NewsEvidenceResponse> evidence,
        boolean scrapped
) {
    public NewsDetailResponse {
        relatedCompanies = List.copyOf(relatedCompanies);
        evidence = List.copyOf(evidence);
    }
}
