package com.cosmos.api.community.dto;

import com.cosmos.api.community.entity.CommunityComment;
import java.time.Instant;
import java.util.UUID;

/**
 * 댓글 작성·수정 응답.
 *
 * @param edited 작성 후 수정된 적이 있는지 (화면의 "수정됨" 표시용)
 */
public record CommentResponse(
        UUID commentId,
        UUID companyId,
        String content,
        Instant createdAt,
        Instant updatedAt,
        boolean edited
) {

    public static CommentResponse from(CommunityComment comment) {
        return new CommentResponse(
                comment.getCommentId(),
                comment.getCompany().getCompanyId(),
                comment.getContent(),
                comment.getCreatedAt(),
                comment.getUpdatedAt(),
                comment.isEdited());
    }
}
