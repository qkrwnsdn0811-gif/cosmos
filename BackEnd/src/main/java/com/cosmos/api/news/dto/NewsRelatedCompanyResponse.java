package com.cosmos.api.news.dto;

import java.math.BigDecimal;
import java.util.UUID;

public record NewsRelatedCompanyResponse(
        UUID companyId,
        String name,
        String sentiment,
        BigDecimal relevanceScore,
        BigDecimal impactScore
) {
}
