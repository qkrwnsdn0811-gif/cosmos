package com.cosmos.api.user.error;

import com.cosmos.api.global.error.ErrorCode;
import org.springframework.http.HttpStatus;

public enum UserErrorCode implements ErrorCode {

    EMAIL_DUPLICATED(HttpStatus.CONFLICT, "이미 사용 중인 이메일입니다."),
    NICKNAME_DUPLICATED(HttpStatus.CONFLICT, "이미 사용 중인 닉네임입니다."),
    /** 이메일이 없는 경우와 비밀번호가 틀린 경우를 일부러 구분하지 않는다 (가입 여부 노출 방지). */
    INVALID_CREDENTIALS(HttpStatus.UNAUTHORIZED, "이메일 또는 비밀번호가 올바르지 않습니다."),
    WATCH_COMPANY_DUPLICATED(HttpStatus.CONFLICT, "이미 등록한 관심 기업입니다."),
    NEWS_SCRAP_DUPLICATED(HttpStatus.CONFLICT, "이미 스크랩한 뉴스입니다."),
    /** 같은 이메일로 인증 코드를 너무 자주 요청함 (메일 폭탄 방지). */
    EMAIL_CODE_TOO_FREQUENT(HttpStatus.TOO_MANY_REQUESTS, "잠시 후 다시 요청해 주세요."),
    /** 인증 코드가 틀림. 정해진 횟수를 넘기면 코드가 폐기된다. */
    EMAIL_CODE_INVALID(HttpStatus.BAD_REQUEST, "인증 코드가 올바르지 않습니다."),
    /** 발송한 코드가 없거나 시간이 지났거나 폐기됨 → 다시 발송받아야 한다. */
    EMAIL_CODE_EXPIRED(HttpStatus.BAD_REQUEST, "인증 코드가 만료되었습니다. 다시 요청해 주세요."),
    /** 이메일 인증을 마치지 않은(또는 30분이 지난) 상태로 가입 시도. */
    EMAIL_NOT_VERIFIED(HttpStatus.FORBIDDEN, "이메일 인증을 먼저 완료해 주세요."),
    /** 서버에 Google 클라이언트 ID 가 설정되지 않아 Google 로그인을 쓸 수 없음. */
    GOOGLE_LOGIN_DISABLED(HttpStatus.SERVICE_UNAVAILABLE, "Google 로그인을 사용할 수 없습니다."),
    /** Google ID 토큰이 위조·만료되었거나 이 서비스용이 아님. */
    GOOGLE_TOKEN_INVALID(HttpStatus.UNAUTHORIZED, "Google 로그인 정보가 올바르지 않습니다. 다시 시도해 주세요."),
    /** Google 이 이메일을 확인하지 않은 계정 (이메일로 기존 회원과 연결할 수 없음). */
    GOOGLE_EMAIL_UNVERIFIED(HttpStatus.FORBIDDEN, "이메일이 확인된 Google 계정으로 로그인해 주세요.");

    private final HttpStatus status;
    private final String message;

    UserErrorCode(HttpStatus status, String message) {
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
