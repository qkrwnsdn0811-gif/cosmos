package com.cosmos.api.company.dto;

import java.util.UUID;

public record IndustryResponse(
        UUID industryId,
        UUID parentIndustryId,
        String name,
        String description,
        long companyCount
) {
}
