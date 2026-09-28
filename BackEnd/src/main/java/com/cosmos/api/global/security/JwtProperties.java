package com.cosmos.api.global.security;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yml의 jwt.* 설정을 담는다.
 *
 * @param secret               토큰 서명 키. 환경변수 JWT_SECRET으로 주입하며 32바이트 이상이어야 한다.
 * @param accessTokenValidity  Access Token 유효기간 (기본 30분)
 * @param refreshTokenValidity Refresh Token 유효기간 (기본 14일)
 */
@ConfigurationProperties(prefix = "jwt")
public record JwtProperties(
        String secret,
        Duration accessTokenValidity,
        Duration refreshTokenValidity
) {

    private static final int MIN_SECRET_BYTES = 32; // HS256 서명에 필요한 최소 길이

    public JwtProperties {
        if (secret == null || secret.isBlank()) {
            throw new IllegalStateException(
                    "jwt.secret이 비어 있습니다. .env.example을 복사해 .env를 만들고 JWT_SECRET을 채워 주세요.");
        }
        if (secret.getBytes().length < MIN_SECRET_BYTES) {
            throw new IllegalStateException(
                    "jwt.secret이 너무 짧습니다. 32바이트 이상이어야 합니다. 생성 예: openssl rand -base64 48");
        }
    }
}
