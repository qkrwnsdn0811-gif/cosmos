package com.cosmos.api.event.dto;

import java.math.BigDecimal;

/** 관측된 시장 대비 초과수익률. windowDays는 이벤트 거래일부터 센 거래일 수(1 = 당일). */
public record AbnormalReturnResponse(
        int windowDays,
        String benchmark,
        BigDecimal stockReturn,
        BigDecimal benchmarkReturn,
        BigDecimal abnormalReturn
) {
}
