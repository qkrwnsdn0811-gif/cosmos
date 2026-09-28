package com.cosmos.api.user.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 가입 전 이메일 인증 정책 (application.yml의 app.email-verification).
 *
 * @param codeTtl        인증 코드가 살아 있는 시간 (기본 5분)
 * @param resendCooldown 같은 이메일로 다시 보낼 수 있기까지의 간격 (기본 60초)
 * @param verifiedTtl    인증 완료 표시가 유지되는 시간. 이 안에 가입을 마쳐야 한다 (기본 30분)
 * @param maxAttempts    코드를 틀릴 수 있는 횟수. 넘으면 코드를 폐기한다 (기본 5회)
 */
@ConfigurationProperties(prefix = "app.email-verification")
public record EmailVerificationProperties(
        Duration codeTtl,
        Duration resendCooldown,
        Duration verifiedTtl,
        int maxAttempts
) {
}
