package com.cosmos.api.user.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.user.config.EmailVerificationProperties;
import com.cosmos.api.user.dto.EmailSendCodeResponse;
import com.cosmos.api.user.dto.EmailVerifyCodeResponse;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

/**
 * 가입 전 이메일 인증. 순서는 "코드 발송 → 코드 확인 → 가입".
 * 발송(sendCode)과 확인(verifyCode)을 맡고, 가입 연동은 다음 이슈(144)에서 붙인다.
 */
@Service
@RequiredArgsConstructor
public class EmailVerificationService {

    private static final int CODE_LENGTH = 6;
    private static final SecureRandom RANDOM = new SecureRandom();

    private final EmailVerificationStore store;
    private final VerificationMailSender mailSender;
    private final UserRepository userRepository;
    private final EmailVerificationProperties properties;

    /**
     * 6자리 코드를 만들어 메일로 보내고 Redis에 저장한다.
     * 이미 가입된 이메일이면 409, 60초 안에 다시 요청하면 429.
     * 메일 발송이 실패하면 코드도 저장하지 않는다 (받지 못한 코드가 남지 않게).
     */
    public EmailSendCodeResponse sendCode(String rawEmail) {
        String email = normalize(rawEmail);

        if (userRepository.existsByEmail(email)) {
            throw new BusinessException(UserErrorCode.EMAIL_DUPLICATED);
        }
        if (store.isInCooldown(email)) {
            throw new BusinessException(UserErrorCode.EMAIL_CODE_TOO_FREQUENT);
        }

        String code = generateCode();
        mailSender.sendCode(email, code, properties.codeTtl());
        store.saveCode(email, code);

        return new EmailSendCodeResponse(email, properties.codeTtl().toSeconds());
    }

    /**
     * 받은 코드를 확인한다. 맞으면 "인증 완료" 표시를 남기고 코드는 폐기한다.
     * 코드가 없거나 만료됐으면 EMAIL_CODE_EXPIRED, 틀리면 EMAIL_CODE_INVALID.
     * 정해진 횟수(기본 5회)를 틀리면 코드를 폐기해 무작위 대입을 막는다.
     */
    public EmailVerifyCodeResponse verifyCode(String rawEmail, String code) {
        String email = normalize(rawEmail);

        String savedCode = store.findCode(email)
                .orElseThrow(() -> new BusinessException(UserErrorCode.EMAIL_CODE_EXPIRED));

        if (!constantTimeEquals(savedCode, code)) {
            long attempts = store.incrementAttempts(email);
            if (attempts >= properties.maxAttempts()) {
                store.deleteCode(email);
            }
            throw new BusinessException(UserErrorCode.EMAIL_CODE_INVALID);
        }

        store.deleteCode(email);
        store.markVerified(email);
        return new EmailVerifyCodeResponse(email, true, properties.verifiedTtl().toSeconds());
    }

    private String normalize(String rawEmail) {
        return rawEmail.toLowerCase(Locale.ROOT);
    }

    // 000000~999999 를 항상 6자리로 (앞자리 0 유지)
    private String generateCode() {
        return String.format("%0" + CODE_LENGTH + "d", RANDOM.nextInt(1_000_000));
    }

    // 문자열을 앞에서부터 비교하다 다른 곳에서 바로 멈추면 걸린 시간으로 코드를 유추할 여지가 생긴다. 길이와 무관하게 같은 시간이 걸리도록 비교한다
    private boolean constantTimeEquals(String expected, String actual) {
        return MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8),
                actual.getBytes(StandardCharsets.UTF_8));
    }
}
