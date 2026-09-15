package com.cosmos.api.company.repository;

import java.util.UUID;

public record CompanySearchRow(
        UUID companyId,
        String name,
        String nameEn,
        String stockCode,
        String market,
        UUID primaryIndustryId,
        String primaryIndustryName,
        int searchRank
) {
}
