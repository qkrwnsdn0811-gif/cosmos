package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

public record RelationshipDetailResponse(
        UUID relationshipId,
        RelationshipCompanyResponse sourceCompany,
        RelationshipCompanyResponse targetCompany,
        String relationshipType,
        String directionality,
        String window,
        BigDecimal score,
        BigDecimal newsScore,
        BigDecimal disclosureScore,
        String impactDirection,
        BigDecimal confidence,
        int evidenceCount,
        Instant asOfAt,
        boolean personalized
) {
}
