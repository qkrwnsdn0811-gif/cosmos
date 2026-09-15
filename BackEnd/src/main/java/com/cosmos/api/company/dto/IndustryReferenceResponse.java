package com.cosmos.api.company.dto;

import java.util.UUID;

public record IndustryReferenceResponse(
        UUID industryId,
        String name
) {
}
