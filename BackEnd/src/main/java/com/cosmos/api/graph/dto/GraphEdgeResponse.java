package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.util.UUID;

public record GraphEdgeResponse(
        UUID relationshipId,
        UUID sourceCompanyId,
        UUID targetCompanyId,
        String relationshipType,
        BigDecimal score,
        BigDecimal newsScore,
        BigDecimal disclosureScore,
        String impactDirection
) {
}
