package com.cosmos.api.global.security;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.user.entity.Role;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.ExpiredJwtException;
import io.jsonwebtoken.JwtException;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.UUID;
import javax.crypto.SecretKey;
import org.springframework.stereotype.Component;

/**
 * JWT(입장권) 발급과 검증을 담당한다.
 * 서명 키 하나로 발급도 하고 검증도 하는 대칭 방식(HS256)이며,
 * 키가 유출되면 토큰을 위조할 수 있으므로 환경변수로만 주입한다.
 */
@Component
public class JwtTokenProvider {

    private static final String CLAIM_ROLE = "role";
    private static final String CLAIM_TYPE = "type";

    private final SecretKey key;
    private final Clock clock;
    private final Duration accessTokenValidity;
    private final Duration refreshTokenValidity;

    public JwtTokenProvider(JwtProperties properties, Clock clock) {
        this.key = Keys.hmacShaKeyFor(properties.secret().getBytes(StandardCharsets.UTF_8));
        this.clock = clock;
        this.accessTokenValidity = properties.accessTokenValidity();
        this.refreshTokenValidity = properties.refreshTokenValidity();
    }

    /** API 호출에 사용하는 짧은 수명의 토큰. 사용자 번호와 권한을 담는다. */
    public String createAccessToken(Long userId, Role role) {
        return create(userId, role, TokenType.ACCESS, accessTokenValidity);
    }

    /** Access Token 재발급에만 사용하는 긴 수명의 토큰. 권한은 담지 않는다. */
    public String createRefreshToken(Long userId) {
        return create(userId, null, TokenType.REFRESH, refreshTokenValidity);
    }

    public Duration refreshTokenValidity() {
        return refreshTokenValidity;
    }

    /**
     * 토큰을 검증하고 로그인 사용자 정보를 꺼낸다.
     *
     * @throws BusinessException 기한이 지났으면 TOKEN_EXPIRED, 위조·형식 오류면 TOKEN_INVALID
     */
    public AuthUser parseAccessToken(String token) {
        Claims claims = parseClaims(token);
        requireType(claims, TokenType.ACCESS);

        String role = claims.get(CLAIM_ROLE, String.class);
        if (role == null) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }
        return new AuthUser(toUserId(claims), toRole(role));
    }

    /**
     * Refresh Token을 검증하고 사용자 번호를 꺼낸다.
     *
     * @throws BusinessException 기한이 지났으면 TOKEN_EXPIRED, 위조·형식 오류면 TOKEN_INVALID
     */
    public Long parseRefreshToken(String token) {
        Claims claims = parseClaims(token);
        requireType(claims, TokenType.REFRESH);
        return toUserId(claims);
    }

    private String create(Long userId, Role role, TokenType type, Duration validity) {
        Instant now = Instant.now(clock);
        var builder = Jwts.builder()
                .subject(String.valueOf(userId))
                .claim(CLAIM_TYPE, type.name())
                // 발급 시각은 초 단위라 같은 초에 두 번 발급하면 토큰이 같아진다.
                // 고유 번호를 넣어 매번 다른 토큰이 되게 하고, 재로그인 시 이전 토큰이 확실히 무효가 되게 한다.
                .id(UUID.randomUUID().toString())
                .issuedAt(Date.from(now))
                .expiration(Date.from(now.plus(validity)))
                .signWith(key);

        if (role != null) {
            builder.claim(CLAIM_ROLE, role.name());
        }
        return builder.compact();
    }

    private Claims parseClaims(String token) {
        try {
            return Jwts.parser()
                    .verifyWith(key)
                    .clock(() -> Date.from(Instant.now(clock)))
                    .build()
                    .parseSignedClaims(token)
                    .getPayload();
        } catch (ExpiredJwtException e) {
            throw new BusinessException(AuthErrorCode.TOKEN_EXPIRED);
        } catch (JwtException | IllegalArgumentException e) {
            // 서명 불일치, 형식 오류, 빈 문자열 등 기한 만료를 제외한 모든 실패
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }
    }

    // Access 자리에 Refresh를 쓰는(또는 그 반대) 오용을 막는다
    private void requireType(Claims claims, TokenType expected) {
        if (!expected.name().equals(claims.get(CLAIM_TYPE, String.class))) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }
    }

    private Long toUserId(Claims claims) {
        try {
            return Long.valueOf(claims.getSubject());
        } catch (NumberFormatException e) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }
    }

    private Role toRole(String role) {
        try {
            return Role.valueOf(role);
        } catch (IllegalArgumentException e) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }
    }
}
