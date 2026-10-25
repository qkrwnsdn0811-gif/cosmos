package com.cosmos.api.market.dto;

import java.math.BigDecimal;
import java.util.List;

/** 지수 하나. change1d·change1m 은 비율(0.0327 = 3.27%), 거래일이 모자라면 null. points 는 오래된 것부터. */
public record MarketIndexResponse(
        String indexCode,
        BigDecimal close,
        Double change1d,
        Double change1m,
        List<IndexPointResponse> points
) {
}
