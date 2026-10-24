package com.cosmos.api.event.dto;

import java.math.BigDecimal;

/**
 * 이벤트 × 기업 × 투자 기간의 방향 딱지. 모델 예측이 아니라 학습 기간(2025-01 ~ 2026-06)에 같은 유형·같은 위치였던
 * 과거 사례의 초과수익률 통계다. horizon은 SHORT(windowDays 3) | LONG(windowDays 20), label은 UP | DOWN | HOLD.
 * caseScope는 COMPANY(이 기업 자신의 사례) | POSITION(같은 위치 기업 전체) | null(비교할 사례 없음).
 * sameShare는 다수 방향으로 움직인 사례 비율, downShare는 내린 사례 비율, meanAr은 평균 초과수익률이다.
 */
public record CompanySignalResponse(
        String horizon,
        int windowDays,
        String label,
        String holdReason,
        String caseScope,
        int caseCount,
        BigDecimal sameShare,
        BigDecimal meanAr,
        BigDecimal downShare
) {
}
