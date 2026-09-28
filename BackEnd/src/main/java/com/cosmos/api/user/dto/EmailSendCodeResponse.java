package com.cosmos.api.user.dto;

/**
 * 인증 코드 발송 응답.
 *
 * @param email            코드를 보낸 주소 (소문자로 정규화된 값)
 * @param expiresInSeconds 코드 유효 시간(초). 프론트가 남은 시간 표시에 쓴다
 */
public record EmailSendCodeResponse(
        String email,
        long expiresInSeconds
) {
}
