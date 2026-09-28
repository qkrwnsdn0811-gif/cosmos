package com.cosmos.api.user.dto;

import com.cosmos.api.user.entity.User;

public record UserResponse(
        Long userId,
        String email,
        String nickname
) {

    public static UserResponse from(User user) {
        return new UserResponse(user.getUserId(), user.getEmail(), user.getNickname());
    }
}
