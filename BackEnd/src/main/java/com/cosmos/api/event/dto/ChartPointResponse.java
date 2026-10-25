package com.cosmos.api.event.dto;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 뉴스 전후 주가 차트의 한 거래일. offset 0이 이벤트 거래일이고, 수익률은 offset -1 종가 대비 누적이다.
 * abnormalReturn = stockReturn - indexReturn. 그날 종가나 기준 종가가 없으면 해당 값이 null이다.
 */
public record ChartPointResponse(
        int offset,
        LocalDate date,
        BigDecimal close,
        BigDecimal indexClose,
        BigDecimal stockReturn,
        BigDecimal indexReturn,
        BigDecimal abnormalReturn
) {
}
