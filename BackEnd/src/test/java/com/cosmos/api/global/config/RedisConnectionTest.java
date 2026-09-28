package com.cosmos.api.global.config;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

/**
 * Redis 연결 확인.
 * Refresh Token을 Redis에 "값 + 유효기간(TTL)" 형태로 저장할 예정이므로,
 * 저장·조회·자동 만료 설정이 실제로 동작하는지 미리 검증한다.
 */
@SpringBootTest
class RedisConnectionTest {

    private static final String TEST_KEY = "test:connection-check";

    @Autowired
    StringRedisTemplate redisTemplate;

    // 상황: 유효기간 1분을 걸고 값을 저장한 뒤 다시 읽음
    // 기대: 저장한 값이 그대로 나오고, 남은 유효기간이 0보다 큼
    @Test
    @DisplayName("Redis에 유효기간을 걸어 값을 저장하고 다시 읽을 수 있다")
    void save_and_read_with_ttl() {
        redisTemplate.opsForValue().set(TEST_KEY, "ok", Duration.ofMinutes(1));

        assertThat(redisTemplate.opsForValue().get(TEST_KEY)).isEqualTo("ok");
        assertThat(redisTemplate.getExpire(TEST_KEY)).isPositive();

        redisTemplate.delete(TEST_KEY); // 테스트 흔적 정리 (Redis는 트랜잭션 롤백이 안 된다)
        assertThat(redisTemplate.hasKey(TEST_KEY)).isFalse();
    }
}
