package com.cosmos.api.global.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Refresh Token 쿠키 설정.
 *
 * @param secure   HTTPS에서만 쿠키를 보낼지. 로컬(http)은 false, 배포는 true.
 * @param sameSite 프론트와 API 주소가 같은 사이트면 Lax, 다른 도메인이면 None (이때 secure는 반드시 true).
 */
@ConfigurationProperties(prefix = "app.auth-cookie")
public record AuthCookieProperties(
        boolean secure,
        String sameSite
) {
}
