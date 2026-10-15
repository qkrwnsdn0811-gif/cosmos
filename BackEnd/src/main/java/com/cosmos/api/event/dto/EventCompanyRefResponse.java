package com.cosmos.api.event.dto;

import java.util.UUID;

public record EventCompanyRefResponse(
        UUID companyId,
        String name,
        String stockCode,
        String market
) {
}
