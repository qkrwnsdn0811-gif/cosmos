package com.cosmos.api.news.dto;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public record NewsSummaryResponse(
        UUID newsId,
        String title,
        String summary,
        String publisher,
        String originalUrl,
        Instant publishedAt,
        String sentiment,
        List<NewsCompanySummaryResponse> relatedCompanies,
        boolean scrapped
) {
    public NewsSummaryResponse {
        relatedCompanies = List.copyOf(relatedCompanies);
    }
}
