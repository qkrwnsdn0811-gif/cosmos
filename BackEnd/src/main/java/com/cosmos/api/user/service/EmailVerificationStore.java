package com.cosmos.api.user.service;

import com.cosmos.api.user.config.EmailVerificationProperties;
import java.util.Optional;
import lombok.RequiredArgsConstructor;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

/**
 * 이메일 인증 상태를 Redis에 보관한다. 모두 TTL로 자동 만료되므로 따로 지우는 작업이 거의 없다.
 *
 * <pre>
 * email-code:{email}      발송한 6자리 코드 (code-ttl)
 * email-cooldown:{email}  최근 발송 표시. 있으면 재발송 거부 (resend-cooldown)
 * email-attempts:{email}  코드를 틀린 횟수 (code-ttl). 상한을 넘으면 코드 폐기
 * email-verified:{email}  인증 완료 표시 (verified-ttl). 이 안에 가입해야 한다
 * </pre>
 * 이메일은 항상 소문자로 정규화해서 키를 만든다 (가입 시 소문자 저장 규칙과 맞춤).
 */
@Component
@RequiredArgsConstructor
public class EmailVerificationStore {

    private static final String CODE_PREFIX = "email-code:";
    private static final String COOLDOWN_PREFIX = "email-cooldown:";
    private static final String ATTEMPTS_PREFIX = "email-attempts:";
    private static final String VERIFIED_PREFIX = "email-verified:";

    private final StringRedisTemplate redisTemplate;
    private final EmailVerificationProperties properties;

    /** 코드를 저장하고 재발송 제한 표시도 함께 남긴다. 새 코드가 나가면 이전 실패 횟수는 지운다. */
    public void saveCode(String email, String code) {
        redisTemplate.opsForValue().set(CODE_PREFIX + email, code, properties.codeTtl());
        redisTemplate.opsForValue().set(COOLDOWN_PREFIX + email, "1", properties.resendCooldown());
        redisTemplate.delete(ATTEMPTS_PREFIX + email);
    }

    public Optional<String> findCode(String email) {
        return Optional.ofNullable(redisTemplate.opsForValue().get(CODE_PREFIX + email));
    }

    public boolean isInCooldown(String email) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(COOLDOWN_PREFIX + email));
    }

    /** 틀린 횟수를 1 올리고 누적 횟수를 돌려준다. 코드와 같은 시간 뒤에 사라진다. */
    public long incrementAttempts(String email) {
        String key = ATTEMPTS_PREFIX + email;
        Long attempts = redisTemplate.opsForValue().increment(key);
        redisTemplate.expire(key, properties.codeTtl());
        return attempts == null ? 0 : attempts;
    }

    /** 코드를 폐기한다 (틀린 횟수 초과 또는 인증 성공). 실패 횟수도 함께 지운다. */
    public void deleteCode(String email) {
        redisTemplate.delete(CODE_PREFIX + email);
        redisTemplate.delete(ATTEMPTS_PREFIX + email);
    }

    /** 인증 완료 표시. 이 시간 안에 가입을 마쳐야 한다. */
    public void markVerified(String email) {
        redisTemplate.opsForValue().set(VERIFIED_PREFIX + email, "1", properties.verifiedTtl());
    }

    public boolean isVerified(String email) {
        return Boolean.TRUE.equals(redisTemplate.hasKey(VERIFIED_PREFIX + email));
    }

    /** 가입이 끝나면 인증 완료 표시를 지운다 (같은 인증으로 두 번 가입하는 일 방지). */
    public void deleteVerified(String email) {
        redisTemplate.delete(VERIFIED_PREFIX + email);
    }

    /** 테스트·재시도용. 이 이메일과 관련된 인증 상태를 모두 지운다. */
    public void clear(String email) {
        redisTemplate.delete(CODE_PREFIX + email);
        redisTemplate.delete(COOLDOWN_PREFIX + email);
        redisTemplate.delete(ATTEMPTS_PREFIX + email);
        redisTemplate.delete(VERIFIED_PREFIX + email);
    }
}
