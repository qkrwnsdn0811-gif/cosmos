package com.cosmos.api.company.dto;

import java.util.List;

/**
 * 관계 기업 한 곳. relationType 은 relationship_type.code(SUPPLY·INVEST·PARTNER·COMPETE·CO_MENTIONED).
 * direction: OUT = 이 기업이 source(방향 있는 관계), IN = 이 기업이 target(방향 있는 관계), NONE = 방향 없는 관계.
 * 같은 상대와 관계가 여러 개면 점수가 가장 높은 것이 대표이고 나머지 종류는 otherTypes(점수 순, 중복 없음).
 */
public record RelatedCompanyResponse(
        String relationType,
        String direction,
        Double score,
        List<String> otherTypes,
        CompanyQuoteResponse quote
) {
}
