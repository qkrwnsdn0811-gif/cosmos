package com.cosmos.api.user.controller;

import com.cosmos.api.global.security.AuthUser;
import com.cosmos.api.global.security.RefreshTokenCookie;
import com.cosmos.api.user.dto.AvailabilityResponse;
import com.cosmos.api.user.dto.LoginRequest;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.dto.TokenResponse;
import com.cosmos.api.user.dto.UserResponse;
import com.cosmos.api.user.service.AuthService;
import com.cosmos.api.user.service.UserService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
@Validated // 쿼리 파라미터 검증 활성화 (check-email, check-nickname)
public class AuthController {

    private final UserService userService;
    private final AuthService authService;
    private final RefreshTokenCookie refreshTokenCookie;

    /** 로그인. Access Token은 본문으로, Refresh Token은 httpOnly 쿠키(Set-Cookie)로 내려준다. */
    @PostMapping("/login")
    public ResponseEntity<TokenResponse> login(@Valid @RequestBody LoginRequest request) {
        AuthService.LoginResult result = authService.login(request);
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, refreshTokenCookie.create(result.refreshToken()).toString())
                .body(new TokenResponse(result.accessToken()));
    }

    /** 로그아웃. 서버의 Refresh Token을 폐기하고, 브라우저의 쿠키도 만료시켜 지운다. */
    @PostMapping("/logout")
    public ResponseEntity<Void> logout(@AuthenticationPrincipal AuthUser authUser) {
        authService.logout(authUser.userId());
        return ResponseEntity.noContent()
                .header(HttpHeaders.SET_COOKIE, refreshTokenCookie.expire().toString())
                .build();
    }

    /**
     * Access Token 재발급. 요청 본문 없이 refresh_token 쿠키로만 동작한다.
     * 실패하면 401을 주는데, 프론트는 code로 재시도(TOKEN_EXPIRED)와 재로그인(TOKEN_INVALID)을 구분한다.
     */
    @PostMapping("/refresh")
    public TokenResponse refresh(
            @CookieValue(name = RefreshTokenCookie.NAME, required = false) String refreshToken
    ) {
        return new TokenResponse(authService.reissueAccessToken(refreshToken));
    }

    /** 회원가입. 성공하면 201과 만들어진 회원 정보(userId, email, nickname)를 돌려준다. */
    @PostMapping("/signup")
    public ResponseEntity<UserResponse> signup(@Valid @RequestBody SignupRequest request) {
        UserResponse response = userService.signup(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /** 이메일 중복 확인. 가입 폼에서 실시간으로 쓰는 공개 API — 쓸 수 있으면 available=true. */
    @GetMapping("/check-email")
    public AvailabilityResponse checkEmail(
            @RequestParam
            @NotBlank(message = "이메일을 입력해 주세요.")
            @Email(message = "올바른 형식의 이메일 주소여야 합니다.")
            @Size(max = 255, message = "이메일은 255자 이하여야 합니다.")
            String email
    ) {
        return new AvailabilityResponse(userService.isEmailAvailable(email));
    }

    /** 닉네임 중복 확인. 가입 폼에서 실시간으로 쓰는 공개 API — 쓸 수 있으면 available=true. */
    @GetMapping("/check-nickname")
    public AvailabilityResponse checkNickname(
            @RequestParam
            @NotBlank(message = "닉네임을 입력해 주세요.")
            @Size(min = 2, max = 30, message = "닉네임은 2자 이상 30자 이하여야 합니다.")
            String nickname
    ) {
        return new AvailabilityResponse(userService.isNicknameAvailable(nickname));
    }
}
