package com.cosmos.api.event.dto;

import java.math.BigDecimal;

/**
 * 적중률 한 줄(투자 기간 × 뉴스 유형). newsType이 null이면 전체 유형 합계다.
 * labeledCount는 UP·DOWN 딱지가 붙은 사례 수, hitCount는 그중 실제 초과수익률 부호가 딱지와 맞은 수다.
 * hitRate = hitCount / labeledCount(딱지가 없으면 null). baseDownShare는 같은 사례에서 실제로 내린 비율이고,
 * baselineRate = max(baseDownShare, 1 - baseDownShare)는 "늘 많은 쪽으로 찍는" 단순 기준선이다.
 */
public record BacktestRowResponse(
        String horizon,
        String newsType,
        String newsTypeLabel,
        int caseCount,
        int labeledCount,
        int hitCount,
        BigDecimal hitRate,
        int upLabels,
        int downLabels,
        BigDecimal baseDownShare,
        BigDecimal baselineRate
) {
}
