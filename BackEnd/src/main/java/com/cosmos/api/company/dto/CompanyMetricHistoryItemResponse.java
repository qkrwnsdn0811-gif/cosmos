package com.cosmos.api.company.dto;

import com.cosmos.api.company.entity.CompanyMetricHistory;
import java.math.BigDecimal;
import java.time.Instant;

public record CompanyMetricHistoryItemResponse(
        Instant measuredAt,
        int newsMentionCount,
        int positiveCount,
        int negativeCount,
        BigDecimal sentimentScore,
        int relationshipCount
) {

    public static CompanyMetricHistoryItemResponse from(CompanyMetricHistory metric) {
        return new CompanyMetricHistoryItemResponse(
                metric.getMeasuredAt(),
                metric.getNewsMentionCount(),
                metric.getPositiveCount(),
                metric.getNegativeCount(),
                metric.getSentimentScore(),
                metric.getRelationshipCount());
    }
}
