package com.cosmos.api.global.security;

import com.cosmos.api.global.error.ErrorCode;
import org.springframework.http.HttpStatus;

/** 인증·인가 실패 시 내려주는 오류 코드. 프론트는 code 값으로 재발급/재로그인을 구분한다. */
public enum AuthErrorCode implements ErrorCode {

    /** 토큰 기한이 지남 → 프론트는 재발급을 시도한다. */
    TOKEN_EXPIRED(HttpStatus.UNAUTHORIZED, "인증이 만료되었습니다."),
    /** 토큰이 없거나 위조·형식 오류 → 프론트는 로그인 화면으로 보낸다. */
    TOKEN_INVALID(HttpStatus.UNAUTHORIZED, "인증 정보가 올바르지 않습니다."),
    /** 로그인은 했지만 권한이 부족함 (예: 일반 사용자가 관리자 API 호출). */
    FORBIDDEN(HttpStatus.FORBIDDEN, "접근 권한이 없습니다.");

    private final HttpStatus status;
    private final String message;

    AuthErrorCode(HttpStatus status, String message) {
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
