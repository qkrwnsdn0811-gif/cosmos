package com.cosmos.api.user.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.global.security.RefreshTokenStore;
import com.cosmos.api.user.dto.LoginRequest;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
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

        if (!passwordEncoder.matches(request.password(), user.getPassword())) {
            throw new BusinessException(UserErrorCode.INVALID_CREDENTIALS);
        }

        String accessToken = jwtTokenProvider.createAccessToken(user.getUserId(), user.getRole());
        String refreshToken = jwtTokenProvider.createRefreshToken(user.getUserId());
        refreshTokenStore.save(user.getUserId(), refreshToken);

        return new LoginResult(accessToken, refreshToken);
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
