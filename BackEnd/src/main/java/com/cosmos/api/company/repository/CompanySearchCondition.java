package com.cosmos.api.company.repository;

import java.util.Locale;
import java.util.UUID;

public record CompanySearchCondition(
        String keyword,
        String market,
        UUID industryId,
        String cursor,
        int size
) {

    public CompanySearchCondition {
        keyword = normalizeKeyword(keyword);
        market = normalizeMarket(market);
        cursor = normalizeNullable(cursor);
    }

    private static String normalizeKeyword(String value) {
        String normalized = normalizeNullable(value);
        return normalized == null ? null : normalized.toLowerCase(Locale.ROOT);
    }

    private static String normalizeMarket(String value) {
        String normalized = normalizeNullable(value);
        return normalized == null ? null : normalized.toUpperCase(Locale.ROOT);
    }

    private static String normalizeNullable(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }
}
