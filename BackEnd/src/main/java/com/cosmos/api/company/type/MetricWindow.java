package com.cosmos.api.company.type;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.util.Arrays;

public enum MetricWindow {

    DAYS_7("7D", 7),
    DAYS_30("30D", 30),
    DAYS_90("90D", 90);

    private final String code;
    private final int days;

    MetricWindow(String code, int days) {
        this.code = code;
        this.days = days;
    }

    public String code() {
        return code;
    }

    public int days() {
        return days;
    }

    public static MetricWindow from(String value) {
        return Arrays.stream(values())
                .filter(window -> window.code.equalsIgnoreCase(value))
                .findFirst()
                .orElseThrow(() -> new BusinessException(CommonErrorCode.VALIDATION_FAILED));
    }
}
