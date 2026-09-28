package com.cosmos.api.community.dto;

import com.cosmos.api.user.entity.User;

/** 댓글 작성자. 화면 표시에 필요한 최소 정보만 담고 이메일은 내보내지 않는다. */
public record CommentAuthorResponse(
        Long userId,
        String nickname
) {

    public static CommentAuthorResponse from(User user) {
        return new CommentAuthorResponse(user.getUserId(), user.getNickname());
    }
}
