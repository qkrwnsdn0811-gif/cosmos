package com.cosmos.api.user.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.global.security.RefreshTokenStore;
import com.cosmos.api.user.dto.LoginRequest;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
import java.security.SecureRandom;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 로그인·토큰 발급 흐름. 회원 정보 자체(가입·조회)는 UserService가 맡는다. */
@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtTokenProvider jwtTokenProvider;
    private final RefreshTokenStore refreshTokenStore;
    private final GoogleIdTokenVerifier googleIdTokenVerifier;

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int NICKNAME_MAX = 30;

    /** 로그인 성공 시 발급한 토큰 두 개. Refresh는 컨트롤러가 쿠키에 담는다. */
    public record LoginResult(String accessToken, String refreshToken) {
    }

    /**
     * 로그인 순서: 이메일로 회원 조회 → 비밀번호 대조 → 토큰 2개 발급 → Refresh는 Redis에 보관.
     * 이메일이 없어도, 비밀번호가 틀려도 같은 오류(INVALID_CREDENTIALS)를 낸다.
     */
    @Transactional(readOnly = true)
    public LoginResult login(LoginRequest request) {
        User user = userRepository.findByEmail(request.email().toLowerCase(Locale.ROOT))
                .orElseThrow(() -> new BusinessException(UserErrorCode.INVALID_CREDENTIALS));

        // Google 로만 가입한 회원은 비밀번호가 없다 — 같은 오류로 막는다
        if (user.getPassword() == null || !passwordEncoder.matches(request.password(), user.getPassword())) {
            throw new BusinessException(UserErrorCode.INVALID_CREDENTIALS);
        }

        return issueTokens(user);
    }

    /**
     * Google 로그인. 브라우저가 받은 Google ID 토큰을 검증한 뒤
     * ① 이 Google 계정이 연결된 회원 → ② 같은 이메일의 회원(연결) → ③ 없으면 새로 가입 순으로 회원을 정하고 토큰을 발급한다.
     * 누구나 쓸 수 있다 — Google 계정이 있으면 따로 가입하지 않아도 된다.
     */
    @Transactional
    public LoginResult loginWithGoogle(String credential) {
        GoogleIdTokenVerifier.GoogleProfile profile = googleIdTokenVerifier.verify(credential);
        User user = userRepository.findByGoogleSub(profile.sub()).orElse(null);
        if (user == null) {
            if (!profile.emailVerified()) {
                throw new BusinessException(UserErrorCode.GOOGLE_EMAIL_UNVERIFIED);
            }
            String email = profile.email().toLowerCase(Locale.ROOT);
            user = userRepository.findByEmail(email).orElse(null);
            if (user != null) {
                user.linkGoogle(profile.sub());
            } else {
                user = userRepository.save(User.createWithGoogle(email, uniqueNickname(profile.name(), email), profile.sub()));
            }
        }
        return issueTokens(user);
    }

    private LoginResult issueTokens(User user) {
        String accessToken = jwtTokenProvider.createAccessToken(user.getUserId(), user.getRole());
        String refreshToken = jwtTokenProvider.createRefreshToken(user.getUserId());
        refreshTokenStore.save(user.getUserId(), refreshToken);
        return new LoginResult(accessToken, refreshToken);
    }

    /** Google 이름(없으면 이메일 앞부분)으로 닉네임을 만들고, 겹치면 뒤에 숫자 4자리를 붙인다. 닉네임은 나중에 바꿀 수 있다. */
    String uniqueNickname(String name, String email) {
        String base = name == null ? "" : name.strip().replaceAll("\\s+", " ");
        if (base.length() < 2) {
            base = email.substring(0, Math.max(0, email.indexOf('@'))).strip();
        }
        if (base.length() < 2) {
            base = "cosmos";
        }
        base = base.substring(0, Math.min(base.length(), NICKNAME_MAX - 5));
        if (!userRepository.existsByNickname(base)) {
            return base;
        }
        for (int i = 0; i < 20; i++) {
            String candidate = base + "-" + (1000 + RANDOM.nextInt(9000));
            if (!userRepository.existsByNickname(candidate)) {
                return candidate;
            }
        }
        return base.substring(0, Math.min(base.length(), NICKNAME_MAX - 13)) + "-" + Long.toString(System.nanoTime(), 36);
    }

    /**
     * 로그아웃. 서버가 보관한 Refresh Token을 지워 더 이상 재발급되지 않게 한다.
     *
     * <p>이미 발급된 Access Token은 회수할 수 없으므로 최대 남은 수명(30분)까지는 유효하다.
     * 프론트도 보관 중인 Access Token을 함께 버려야 한다.
     */
    public void logout(Long userId) {
        refreshTokenStore.delete(userId);
    }

    /**
     * Access Token 재발급.
     *
     * <p>쿠키의 Refresh Token을 검증한 뒤, 서버(Redis)가 보관한 것과 같은지까지 대조한다.
     * 로그아웃했거나 다른 기기에서 다시 로그인해 교체된 토큰은 서버 사본과 달라져 거부된다.
     *
     * @throws BusinessException 쿠키가 없거나 대조 실패면 TOKEN_INVALID, 기한이 지났으면 TOKEN_EXPIRED
     */
    @Transactional(readOnly = true)
    public String reissueAccessToken(String refreshToken) {
        if (refreshToken == null || refreshToken.isBlank()) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }

        Long userId = jwtTokenProvider.parseRefreshToken(refreshToken);

        boolean matchesStored = refreshTokenStore.find(userId)
                .filter(stored -> stored.equals(refreshToken))
                .isPresent();
        if (!matchesStored) {
            throw new BusinessException(AuthErrorCode.TOKEN_INVALID);
        }

        // 권한이 바뀌었거나 탈퇴했을 수 있으므로 현재 회원 정보를 다시 읽는다
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(AuthErrorCode.TOKEN_INVALID));

        return jwtTokenProvider.createAccessToken(user.getUserId(), user.getRole());
    }
}
