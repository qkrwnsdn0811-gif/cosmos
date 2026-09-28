package com.cosmos.api.company.repository;

import java.util.UUID;

public record IndustryListRow(
        UUID industryId,
        UUID parentIndustryId,
        String name,
        String description,
        long companyCount
) {
}
