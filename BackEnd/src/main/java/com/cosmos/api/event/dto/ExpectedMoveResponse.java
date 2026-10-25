package com.cosmos.api.event.dto;

import java.math.BigDecimal;

/**
 * 종목의 예상 변동폭(방향과 무관한 크기). 학습 기간(~2026-06) 이 종목의 뉴스일 뒤 |초과수익률| 평균이며 뉴스 유형과
 * 무관하게 종목마다 하나다. horizon은 SHORT(windowDays 3) | LONG(windowDays 20), grade는 같은 밸류체인 종목을 셋으로 나눈
 * SMALL | MID | LARGE, caseCount는 평균에 쓴 뉴스일 수다. 검증은 BacktestResponse.moveRows에 있다.
 */
public record ExpectedMoveResponse(
        String horizon,
        int windowDays,
        BigDecimal expectedMove,
        int caseCount,
        String grade
) {
}
