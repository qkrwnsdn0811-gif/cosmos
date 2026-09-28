package com.cosmos.api.company.type;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.time.ZonedDateTime;
import java.util.Arrays;

public enum StockPeriod {

    MONTH_1("1M", 1, 0),
    MONTH_3("3M", 3, 0),
    MONTH_6("6M", 6, 0),
    YEAR_1("1Y", 0, 1);

    private final String code;
    private final int months;
    private final int years;

    StockPeriod(String code, int months, int years) {
        this.code = code;
        this.months = months;
        this.years = years;
    }

    public String code() {
        return code;
    }

    public ZonedDateTime subtractFrom(ZonedDateTime value) {
        return value.minusYears(years).minusMonths(months);
    }

    public static StockPeriod from(String value) {
        return Arrays.stream(values())
                .filter(period -> period.code.equalsIgnoreCase(value))
                .findFirst()
                .orElseThrow(() -> new BusinessException(CommonErrorCode.VALIDATION_FAILED));
    }
}
