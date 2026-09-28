package com.cosmos.api.user.controller;

import com.cosmos.api.global.security.AuthUser;
import com.cosmos.api.user.dto.NicknameUpdateRequest;
import com.cosmos.api.user.dto.UserResponse;
import com.cosmos.api.user.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 로그인한 사용자의 내 정보 API. 모두 Access Token이 필요하다. */
@RestController
@RequestMapping("/api/users/me")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    /** 내 정보 조회. 새로고침 직후 프론트가 "지금 로그인한 사람"을 그릴 때 사용한다. */
    @GetMapping
    public UserResponse getMe(@AuthenticationPrincipal AuthUser authUser) {
        return userService.getMe(authUser.userId());
    }

    /** 내 닉네임 수정. 응답 형식은 내 정보 조회와 같다. */
    @PatchMapping
    public UserResponse updateNickname(
            @AuthenticationPrincipal AuthUser authUser,
            @Valid @RequestBody NicknameUpdateRequest request
    ) {
        return userService.updateNickname(authUser.userId(), request.nickname());
    }
}
