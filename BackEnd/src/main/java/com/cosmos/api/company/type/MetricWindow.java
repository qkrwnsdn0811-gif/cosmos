package com.cosmos.api.company.type;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.time.LocalDate;
import java.time.Period;
import java.util.Arrays;

public enum MetricWindow {

    DAYS_30("30D", Period.ofDays(30)),
    YEAR_1("1Y", Period.ofYears(1)),
    YEARS_10("10Y", Period.ofYears(10));

    private final String code;
    private final Period period;

    MetricWindow(String code, Period period) {
        this.code = code;
        this.period = period;
    }

    public String code() {
        return code;
    }

    public LocalDate inclusiveStartDate(LocalDate endDate) {
        return endDate.minus(period).plusDays(1);
    }

    public static MetricWindow from(String value) {
        return Arrays.stream(values())
                .filter(window -> window.code.equalsIgnoreCase(value))
                .findFirst()
                .orElseThrow(() -> new BusinessException(CommonErrorCode.VALIDATION_FAILED));
    }
}
