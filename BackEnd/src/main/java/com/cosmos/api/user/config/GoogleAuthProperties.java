package com.cosmos.api.user.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * application.yml의 app.google.* 설정.
 *
 * @param clientId Google Cloud 콘솔에서 만든 "웹 애플리케이션" OAuth 클라이언트 ID (환경변수 GOOGLE_CLIENT_ID).
 *                 비어 있으면 Google 로그인을 끈다. ID 토큰 검증만 하므로 클라이언트 보안 비밀은 필요 없다.
 */
@ConfigurationProperties(prefix = "app.google")
public record GoogleAuthProperties(String clientId) {

    public boolean enabled() {
        return clientId != null && !clientId.isBlank();
    }
}
