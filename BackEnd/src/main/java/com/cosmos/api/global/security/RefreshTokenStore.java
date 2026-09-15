package com.cosmos.api.global.security;

import java.time.Duration;
import java.util.Optional;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

/**
 * Refresh Token을 Redis에 보관한다. 키는 "refresh:{userId}" 하나라 사용자당 토큰은 항상 1개다.
 *
 * <p>서버가 사본을 들고 있어야 로그아웃 시 폐기할 수 있고, 유효기간은 Redis TTL로 자동 만료시킨다.
 */
@Component
public class RefreshTokenStore {

    private static final String KEY_PREFIX = "refresh:";

    private final StringRedisTemplate redisTemplate;
    private final Duration validity;

    public RefreshTokenStore(StringRedisTemplate redisTemplate, JwtTokenProvider jwtTokenProvider) {
        this.redisTemplate = redisTemplate;
        this.validity = jwtTokenProvider.refreshTokenValidity();
    }

    /** 저장. 이미 있으면 덮어써서 이전 로그인의 토큰은 무효가 된다. */
    public void save(Long userId, String refreshToken) {
        redisTemplate.opsForValue().set(key(userId), refreshToken, validity);
    }

    public Optional<String> find(Long userId) {
        return Optional.ofNullable(redisTemplate.opsForValue().get(key(userId)));
    }

    /** 로그아웃 등으로 폐기. 이후 재발급 요청은 거부된다. */
    public void delete(Long userId) {
        redisTemplate.delete(key(userId));
    }

    private String key(Long userId) {
        return KEY_PREFIX + userId;
    }
}
