package com.cosmos.api.event.dto;

import java.util.List;

/**
 * 기업 한 곳의 과거 뉴스 한 건과 그때 주가 반응(F13). basis는 이 기업이 기사에 나왔는지(REPORTED) 추정인지(INFERRED),
 * position·signals는 이 기업의 위치와 방향 딱지(없으면 null·빈 목록), abnormalReturns는 이 기업의 관측 초과수익률(1·3·5·20거래일)이다.
 */
public record CompanyHistoryItemResponse(
        EventSummaryResponse event,
        String basis,
        String position,
        String positionLabel,
        List<CompanySignalResponse> signals,
        List<AbnormalReturnResponse> abnormalReturns
) {
    public CompanyHistoryItemResponse {
        signals = List.copyOf(signals);
        abnormalReturns = List.copyOf(abnormalReturns);
    }
}
