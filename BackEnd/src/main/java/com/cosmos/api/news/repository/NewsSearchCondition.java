package com.cosmos.api.news.repository;

import com.cosmos.api.news.type.NewsSentiment;
import java.time.LocalDate;
import java.util.UUID;

public record NewsSearchCondition(
        String keyword,
        UUID companyId,
        UUID industryId,
        String sentiment,
        LocalDate from,
        LocalDate to,
        String cursor,
        int size
) {
    public NewsSearchCondition {
        keyword = normalizeNullable(keyword);
        sentiment = NewsSentiment.normalize(sentiment);
        cursor = normalizeNullable(cursor);
    }

    private static String normalizeNullable(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }
}
