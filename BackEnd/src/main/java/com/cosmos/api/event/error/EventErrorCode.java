package com.cosmos.api.event.error;

import com.cosmos.api.global.error.ErrorCode;
import org.springframework.http.HttpStatus;

public enum EventErrorCode implements ErrorCode {

    EVENT_NOT_FOUND(HttpStatus.NOT_FOUND, "이벤트를 찾을 수 없습니다."),
    EVENT_COMPANY_NOT_FOUND(HttpStatus.NOT_FOUND, "이 이벤트의 영향 기업이 아닙니다."),
    EVENT_CHART_UNAVAILABLE(HttpStatus.NOT_FOUND, "주가 차트를 그릴 수 없는 이벤트입니다(거래일 미정 또는 국내 상장사 아님).");

    private final HttpStatus status;
    private final String message;

    EventErrorCode(HttpStatus status, String message) {
        this.status = status;
        this.message = message;
    }

    @Override
    public HttpStatus status() {
        return status;
    }

    @Override
    public String code() {
        return name();
    }

    @Override
    public String message() {
        return message;
    }
}
