package com.cosmos.api.global.security;

import java.time.Duration;
import org.springframework.http.ResponseCookie;
import org.springframework.stereotype.Component;

/**
 * Refresh Token을 담는 httpOnly 쿠키를 만든다.
 *
 * <p>httpOnly라 브라우저의 JS가 읽을 수 없어 XSS로 훔칠 수 없고,
 * Path를 /api/auth로 제한해 재발급·로그아웃 요청에만 자동 첨부된다.
 */
@Component
public class RefreshTokenCookie {

    public static final String NAME = "refresh_token";
    private static final String PATH = "/api/auth";

    private final AuthCookieProperties properties;
    private final Duration validity;

    public RefreshTokenCookie(AuthCookieProperties properties, JwtTokenProvider jwtTokenProvider) {
        this.properties = properties;
        this.validity = jwtTokenProvider.refreshTokenValidity();
    }

    public ResponseCookie create(String refreshToken) {
        return base(refreshToken).maxAge(validity).build();
    }

    /** 로그아웃 시 브라우저의 쿠키를 지우기 위해 수명 0인 빈 쿠키를 내려준다. */
    public ResponseCookie expire() {
        return base("").maxAge(Duration.ZERO).build();
    }

    private ResponseCookie.ResponseCookieBuilder base(String value) {
        return ResponseCookie.from(NAME, value)
                .httpOnly(true)
                .secure(properties.secure())
                .sameSite(properties.sameSite())
                .path(PATH);
    }
}
