package com.cosmos.api.global.security;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.user.entity.Role;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 토큰 발급·검증 단위 테스트.
 * 시계를 직접 고정해서 "기한이 지난 상황"을 기다리지 않고 만들어 낸다.
 */
class JwtTokenProviderTest {

    private static final String SECRET = "unit-test-secret-key-1234567890-abcdefghijklmnop";
    private static final Instant NOW = Instant.parse("2026-09-11T00:00:00Z");

    private final JwtProperties properties =
            new JwtProperties(SECRET, Duration.ofMinutes(30), Duration.ofDays(14));
    private final JwtTokenProvider provider =
            new JwtTokenProvider(properties, Clock.fixed(NOW, ZoneOffset.UTC));

    // 시간이 흐른 뒤의 서버를 흉내 내는 검증기
    private JwtTokenProvider providerAt(Instant time) {
        return new JwtTokenProvider(properties, Clock.fixed(time, ZoneOffset.UTC));
    }

    // 상황: Access Token을 발급하고 곧바로 검증
    // 기대: 사용자 번호와 권한이 그대로 나온다
    @Test
    @DisplayName("Access Token에서 사용자 번호와 권한을 꺼낼 수 있다")
    void access_token_round_trip() {
        String token = provider.createAccessToken(42L, Role.ROLE_USER);

        AuthUser user = provider.parseAccessToken(token);

        assertThat(user.userId()).isEqualTo(42L);
        assertThat(user.role()).isEqualTo(Role.ROLE_USER);
    }

    // 상황: Refresh Token을 발급하고 검증
    // 기대: 사용자 번호가 그대로 나온다
    @Test
    @DisplayName("Refresh Token에서 사용자 번호를 꺼낼 수 있다")
    void refresh_token_round_trip() {
        String token = provider.createRefreshToken(7L);

        assertThat(provider.parseRefreshToken(token)).isEqualTo(7L);
    }

    // 상황: 발급 후 31분이 지난 시점에 Access Token 검증 (유효기간 30분)
    // 기대: TOKEN_EXPIRED → 프론트는 재발급을 시도해야 한다
    @Test
    @DisplayName("유효기간이 지난 토큰은 TOKEN_EXPIRED")
    void expired_token() {
        String token = provider.createAccessToken(1L, Role.ROLE_USER);

        assertThatThrownBy(() -> providerAt(NOW.plus(Duration.ofMinutes(31))).parseAccessToken(token))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.TOKEN_EXPIRED);
    }

    // 상황: 토큰 뒷부분(서명)을 한 글자 바꿔치기해서 검증
    // 기대: TOKEN_INVALID → 프론트는 로그인 화면으로 보내야 한다
    @Test
    @DisplayName("위조된 토큰은 TOKEN_INVALID")
    void tampered_token() {
        String token = provider.createAccessToken(1L, Role.ROLE_USER);
        String tampered = token.substring(0, token.length() - 1) + (token.endsWith("A") ? "B" : "A");

        assertThatThrownBy(() -> provider.parseAccessToken(tampered))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.TOKEN_INVALID);
    }

    // 상황: Refresh Token을 Access Token 자리에 사용
    // 기대: 용도가 다르므로 TOKEN_INVALID (재발급 전용 토큰으로 API 호출 차단)
    @Test
    @DisplayName("Refresh Token으로는 API를 호출할 수 없다")
    void refresh_token_cannot_be_used_as_access() {
        String refreshToken = provider.createRefreshToken(1L);

        assertThatThrownBy(() -> provider.parseAccessToken(refreshToken))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.TOKEN_INVALID);
    }

    // 상황: JWT 모양이 아닌 아무 문자열로 검증
    // 기대: TOKEN_INVALID
    @Test
    @DisplayName("토큰 형식이 아니면 TOKEN_INVALID")
    void malformed_token() {
        assertThatThrownBy(() -> provider.parseAccessToken("not-a-token"))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode())
                .isEqualTo(AuthErrorCode.TOKEN_INVALID);
    }

    // 상황: 서명 키가 32바이트보다 짧은 설정으로 시작
    // 기대: 애플리케이션이 뜨지 않도록 예외 발생 (약한 키 방지)
    @Test
    @DisplayName("서명 키가 너무 짧으면 설정 단계에서 막는다")
    void short_secret_rejected() {
        assertThatThrownBy(() -> new JwtProperties("short", Duration.ofMinutes(30), Duration.ofDays(14)))
                .isInstanceOf(IllegalStateException.class);
    }
}
