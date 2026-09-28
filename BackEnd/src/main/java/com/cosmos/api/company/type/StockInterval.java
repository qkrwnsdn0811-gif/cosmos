package com.cosmos.api.company.type;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;

public enum StockInterval {

    DAY_1("1D");

    private final String code;

    StockInterval(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static StockInterval from(String value) {
        if (DAY_1.code.equalsIgnoreCase(value)) {
            return DAY_1;
        }
        throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
    }
}
