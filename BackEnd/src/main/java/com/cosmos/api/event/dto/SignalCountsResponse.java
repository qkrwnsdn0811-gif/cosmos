package com.cosmos.api.event.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 이벤트 한 건의 방향 딱지 개수(영향 기업 전체, event_company_signal 기준). JSON 키는 투자 기간 코드 그대로
 * SHORT(3거래일) · LONG(20거래일)이다. 딱지가 없는 이벤트는 모두 0이다.
 */
public record SignalCountsResponse(
        @JsonProperty("SHORT") HorizonCount shortTerm,
        @JsonProperty("LONG") HorizonCount longTerm
) {

    public static SignalCountsResponse empty() {
        return new SignalCountsResponse(HorizonCount.ZERO, HorizonCount.ZERO);
    }

    /** 투자 기간 하나의 UP · DOWN · HOLD 딱지 수. */
    public record HorizonCount(int up, int down, int hold) {

        public static final HorizonCount ZERO = new HorizonCount(0, 0, 0);
    }
}
