package com.cosmos.api.community.dto;

import com.cosmos.api.community.entity.CommunityComment;
import java.time.Instant;
import java.util.UUID;

/** 기업별 댓글 목록의 한 줄. 이미 어느 기업인지 아는 화면이라 기업 이름은 담지 않는다. */
public record CompanyCommentResponse(
        UUID commentId,
        String content,
        CommentAuthorResponse author,
        UUID companyId,
        Instant createdAt,
        Instant updatedAt,
        boolean edited
) {

    public static CompanyCommentResponse from(CommunityComment comment) {
        return new CompanyCommentResponse(
                comment.getCommentId(),
                comment.getContent(),
                CommentAuthorResponse.from(comment.getUser()),
                comment.getCompany().getCompanyId(),
                comment.getCreatedAt(),
                comment.getUpdatedAt(),
                comment.isEdited());
    }
}
