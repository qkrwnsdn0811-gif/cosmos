package com.cosmos.api.event.repository;

import java.util.UUID;

/** 이벤트 목록 조건. valueChain·companyId가 null이면 그 조건을 걸지 않는다. */
public record EventSearchCondition(
        String valueChain,
        UUID companyId,
        boolean hasInferred
) {
}
