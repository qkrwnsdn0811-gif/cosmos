package com.cosmos.api.global.security;

import com.cosmos.api.user.entity.Role;

/**
 * 토큰에서 꺼낸 로그인 사용자 정보.
 * 컨트롤러에서 {@code @AuthenticationPrincipal AuthUser user} 로 받아 쓴다.
 */
public record AuthUser(
        Long userId,
        Role role
) {
}
