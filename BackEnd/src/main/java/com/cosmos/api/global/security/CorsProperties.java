package com.cosmos.api.global.security;

import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 쿠키를 주고받을 프론트 주소 목록. 쿠키 인증(credentials)에는 "*"를 쓸 수 없어 정확한 주소가 필요하다.
 *
 * @param allowedOrigins 예: http://localhost:5173 (쉼표로 여러 개)
 */
@ConfigurationProperties(prefix = "app.cors")
public record CorsProperties(
        List<String> allowedOrigins
) {
}
