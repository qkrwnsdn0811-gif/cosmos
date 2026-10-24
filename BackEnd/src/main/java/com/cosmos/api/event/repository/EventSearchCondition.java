package com.cosmos.api.event.repository;

import java.util.UUID;

/**
 * 이벤트 목록 조건. valueChain은 서비스에서 필수로 검사한다. companyId·newsType이 null이면 그 조건을 걸지 않는다.
 * newsType은 서비스에서 알려진 뉴스 유형 코드인지 검사한다.
 */
public record EventSearchCondition(
        String valueChain,
        UUID companyId,
        String newsType,
        boolean hasInferred
) {
}
