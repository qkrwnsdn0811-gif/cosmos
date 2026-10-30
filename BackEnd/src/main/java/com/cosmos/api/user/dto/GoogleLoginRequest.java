package com.cosmos.api.user.dto;

import jakarta.validation.constraints.NotBlank;

/** Google 로그인 요청. credential = Google Identity Services 가 브라우저에 준 ID 토큰(JWT). */
public record GoogleLoginRequest(
        @NotBlank(message = "Google 로그인 정보가 없습니다.")
        String credential
) {
}
