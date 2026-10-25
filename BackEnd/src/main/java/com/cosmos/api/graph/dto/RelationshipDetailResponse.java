package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.UUID;

/** ownership 은 투자·지분 관계에 공시 지분이 있을 때만 값이 있다 (그 밖에는 null). */
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
        boolean personalized,
        OwnershipDisclosureResponse ownership
) {
}
