package com.cosmos.api.event.dto;

import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

/**
 * 뉴스 전후 주가 차트(F7). benchmark는 기업 시장의 지수(KOSPI | KOSDAQ)이고, 그 지수의 거래일을 달력으로 쓴다.
 * points는 offset -5 ~ +20(달력이 끝나면 그보다 짧다), 오래된 순이다.
 */
public record EventChartResponse(
        UUID eventId,
        UUID companyId,
        LocalDate tradingDate,
        String benchmark,
        List<ChartPointResponse> points
) {
    public EventChartResponse {
        points = List.copyOf(points);
    }
}
