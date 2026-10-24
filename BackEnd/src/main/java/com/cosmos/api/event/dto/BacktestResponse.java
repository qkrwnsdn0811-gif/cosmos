package com.cosmos.api.event.dto;

import java.time.LocalDate;
import java.util.List;

/**
 * 방향 딱지 적중률(F11). 딱지는 trainEnd까지의 과거 사례로만 만들고, testStart ~ testEnd 뉴스에서 맞았는지 잰다.
 * rows는 SHORT 먼저, 각 기간 안에서 전체 유형 줄 다음 딱지 수(labeledCount) 내림차순이다. 계산 전이면 rows가 비고 날짜는 null이다.
 */
public record BacktestResponse(
        String valueChain,
        LocalDate trainEnd,
        LocalDate testStart,
        LocalDate testEnd,
        List<BacktestRowResponse> rows
) {
    public BacktestResponse {
        rows = List.copyOf(rows);
    }
}
