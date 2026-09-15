package com.cosmos.api.graph.dto;

import java.util.UUID;

public record GraphNodeResponse(
        UUID companyId,
        String name,
        String stockCode,
        String market,
        String industryName
) {
}
