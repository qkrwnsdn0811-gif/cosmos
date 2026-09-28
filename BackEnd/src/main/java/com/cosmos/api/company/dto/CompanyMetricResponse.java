package com.cosmos.api.company.dto;

import com.cosmos.api.company.entity.CompanyMetricHistory;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

public record CompanyMetricResponse(
        UUID companyId,
        String window,
        Integer newsMentionCount,
        Integer positiveCount,
        Integer negativeCount,
        BigDecimal sentimentScore,
        Integer relationshipCount,
        Instant measuredAt
) {

    public static CompanyMetricResponse empty(UUID companyId, String window) {
        return new CompanyMetricResponse(companyId, window, null, null, null, null, null, null);
    }

    public static CompanyMetricResponse from(UUID companyId, String window, CompanyMetricHistory metric) {
        return new CompanyMetricResponse(
                companyId,
                window,
                metric.getNewsMentionCount(),
                metric.getPositiveCount(),
                metric.getNegativeCount(),
                metric.getSentimentScore(),
                metric.getRelationshipCount(),
                metric.getMeasuredAt());
    }
}
