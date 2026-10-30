package com.cosmos.api.user.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.user.config.GoogleAuthProperties;
import com.cosmos.api.user.error.UserErrorCode;
import java.util.List;
import java.util.Set;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtException;
import org.springframework.security.oauth2.jwt.JwtTimestampValidator;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.stereotype.Component;

/**
 * 브라우저의 "Google 로 로그인" 버튼(Google Identity Services)이 넘겨준 ID 토큰을 검증한다.
 *
 * <p>Google 공개키(JWKS)로 서명을 확인하고, 발급자(accounts.google.com) · 대상(우리 클라이언트 ID) · 만료를 본다.
 * 공개키는 Nimbus 가 받아 캐시하고 Google 이 키를 바꾸면 다시 받는다.
 */
@Component
public class GoogleIdTokenVerifier {

    private static final String JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs";
    private static final Set<String> ISSUERS = Set.of("accounts.google.com", "https://accounts.google.com");

    /** ID 토큰에서 쓰는 값만 꺼낸 것. */
    public record GoogleProfile(String sub, String email, boolean emailVerified, String name) {
    }

    private final GoogleAuthProperties properties;
    private volatile JwtDecoder decoder;

    public GoogleIdTokenVerifier(GoogleAuthProperties properties) {
        this.properties = properties;
    }

    public boolean enabled() {
        return properties.enabled();
    }

    public String clientId() {
        return properties.clientId();
    }

    /**
     * @throws BusinessException 설정이 없으면 GOOGLE_LOGIN_DISABLED, 토큰이 올바르지 않으면 GOOGLE_TOKEN_INVALID
     */
    public GoogleProfile verify(String credential) {
        if (!enabled()) {
            throw new BusinessException(UserErrorCode.GOOGLE_LOGIN_DISABLED);
        }
        if (credential == null || credential.isBlank()) {
            throw new BusinessException(UserErrorCode.GOOGLE_TOKEN_INVALID);
        }
        Jwt jwt;
        try {
            jwt = decoder().decode(credential);
        } catch (JwtException e) {
            throw new BusinessException(UserErrorCode.GOOGLE_TOKEN_INVALID);
        }
        String sub = jwt.getSubject();
        String email = jwt.getClaimAsString("email");
        if (sub == null || sub.isBlank() || email == null || email.isBlank()) {
            throw new BusinessException(UserErrorCode.GOOGLE_TOKEN_INVALID);
        }
        Object verified = jwt.getClaim("email_verified");
        boolean emailVerified = Boolean.TRUE.equals(verified) || "true".equals(String.valueOf(verified));
        return new GoogleProfile(sub, email, emailVerified, jwt.getClaimAsString("name"));
    }

    private JwtDecoder decoder() {
        JwtDecoder d = decoder;
        if (d == null) {
            synchronized (this) {
                if (decoder == null) {
                    NimbusJwtDecoder nimbus = NimbusJwtDecoder.withJwkSetUri(JWKS_URI).build();
                    String clientId = properties.clientId();
                    nimbus.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
                            new JwtTimestampValidator(),
                            jwt -> ISSUERS.contains(String.valueOf(jwt.getClaims().get("iss")))
                                    ? OAuth2TokenValidatorResult.success()
                                    : OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token", "iss", null)),
                            jwt -> {
                                List<String> aud = jwt.getAudience();
                                return aud != null && aud.contains(clientId)
                                        ? OAuth2TokenValidatorResult.success()
                                        : OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token", "aud", null));
                            }));
                    decoder = nimbus;
                }
                d = decoder;
            }
        }
        return d;
    }
}
