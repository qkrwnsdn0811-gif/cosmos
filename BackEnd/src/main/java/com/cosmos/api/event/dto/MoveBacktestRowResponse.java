package com.cosmos.api.event.dto;

import java.math.BigDecimal;

/**
 * 예상 변동폭 검증 한 줄(투자 기간 × 등급). 학습 기간 뒤 뉴스일에서 그 등급 종목의 예상 변동폭 평균(predictedMove)과
 * 실제 |초과수익률| 평균(realizedMove)을 나란히 둔다. caseCount는 검증한 뉴스일 수다.
 */
public record MoveBacktestRowResponse(
        String horizon,
        int windowDays,
        String grade,
        int caseCount,
        BigDecimal predictedMove,
        BigDecimal realizedMove
) {
}
