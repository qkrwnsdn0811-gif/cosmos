package com.cosmos.api.graph.dto;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 투자·지분 관계의 공시 근거 (NEXUS, V16 company_ownership — OpenDART 타법인 출자 현황).
 * stakePct 는 기말 지분율(23.7 = 23.7%), settledOn 은 보고서 결산일, sourceUrl 은 DART 공시 원문 주소.
 */
public record OwnershipDisclosureResponse(
        BigDecimal stakePct,
        String purpose,
        LocalDate settledOn,
        String receiptNo,
        String sourceUrl
) {
}
