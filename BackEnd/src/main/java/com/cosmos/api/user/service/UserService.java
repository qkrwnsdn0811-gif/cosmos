package com.cosmos.api.user.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.dto.UserResponse;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class UserService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    /** 내 정보 조회. 토큰의 사용자 번호로 찾으며, 탈퇴했으면 토큰이 유효해도 거부한다. */
    @Transactional(readOnly = true)
    public UserResponse getMe(Long userId) {
        return UserResponse.from(findActiveUser(userId));
    }

    /**
     * 닉네임 수정. 다른 사람이 쓰는 닉네임이면 409.
     * 지금과 같은 닉네임으로 바꾸는 것은 아무 일도 하지 않고 성공으로 본다.
     */
    @Transactional
    public UserResponse updateNickname(Long userId, String nickname) {
        User user = findActiveUser(userId);

        if (!user.getNickname().equals(nickname)) {
            if (userRepository.existsByNickname(nickname)) {
                throw new BusinessException(UserErrorCode.NICKNAME_DUPLICATED);
            }
            user.changeNickname(nickname);
        }
        return UserResponse.from(user);
    }

    // 토큰은 30분간 살아 있어서, 그 사이 탈퇴한 회원의 토큰이 들어올 수 있다
    private User findActiveUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(AuthErrorCode.TOKEN_INVALID));
    }

    /** 이메일 사용 가능 여부. 저장된 이메일은 전부 소문자이므로 소문자로 바꿔 비교한다. */
    @Transactional(readOnly = true)
    public boolean isEmailAvailable(String email) {
        return !userRepository.existsByEmail(email.toLowerCase(Locale.ROOT));
    }

    /** 닉네임 사용 가능 여부. */
    @Transactional(readOnly = true)
    public boolean isNicknameAvailable(String nickname) {
        return !userRepository.existsByNickname(nickname);
    }

    /**
     * 회원가입 순서: 이메일 소문자 정규화 → 중복 검사 → 비밀번호 암호화 → 저장.
     * 중복 검사를 통과해도 극히 드물게 동시 가입이 겹칠 수 있는데,
     * 그때는 DB의 유니크 인덱스가 최종 방어선이 된다.
     */
    @Transactional
    public UserResponse signup(SignupRequest request) {
        String email = request.email().toLowerCase(Locale.ROOT);

        if (userRepository.existsByEmail(email)) {
            throw new BusinessException(UserErrorCode.EMAIL_DUPLICATED);
        }
        if (userRepository.existsByNickname(request.nickname())) {
            throw new BusinessException(UserErrorCode.NICKNAME_DUPLICATED);
        }

        User user = User.create(email, passwordEncoder.encode(request.password()), request.nickname());
        return UserResponse.from(userRepository.save(user));
    }
}
