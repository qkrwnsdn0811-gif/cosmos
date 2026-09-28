package com.cosmos.api.user.dto;

/** 로그인·재발급 응답. Refresh Token은 본문이 아니라 httpOnly 쿠키로 나가므로 여기 없다. */
public record TokenResponse(
        String accessToken
) {
}
