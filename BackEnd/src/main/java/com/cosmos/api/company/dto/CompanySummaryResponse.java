package com.cosmos.api.company.dto;

import java.util.UUID;

public record CompanySummaryResponse(
        UUID companyId,
        String name,
        String nameEn,
        String stockCode,
        String market,
        IndustryReferenceResponse primaryIndustry,
        boolean watched
) {
}
