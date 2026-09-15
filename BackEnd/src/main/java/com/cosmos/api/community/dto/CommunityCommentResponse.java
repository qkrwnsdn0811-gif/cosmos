package com.cosmos.api.community.dto;

import com.cosmos.api.community.entity.CommunityComment;
import com.cosmos.api.company.entity.Company;
import java.time.Instant;
import java.util.UUID;

/** 전체 커뮤니티 목록의 한 줄. 여러 기업의 댓글이 섞이므로 어느 기업 글인지 함께 담는다. */
public record CommunityCommentResponse(
        UUID commentId,
        String content,
        CommentAuthorResponse author,
        CompanyBrief company,
        Instant createdAt,
        Instant updatedAt,
        boolean edited
) {

    public record CompanyBrief(UUID companyId, String name) {

        public static CompanyBrief from(Company company) {
            return new CompanyBrief(company.getCompanyId(), company.getName());
        }
    }

    public static CommunityCommentResponse from(CommunityComment comment) {
        return new CommunityCommentResponse(
                comment.getCommentId(),
                comment.getContent(),
                CommentAuthorResponse.from(comment.getUser()),
                CompanyBrief.from(comment.getCompany()),
                comment.getCreatedAt(),
                comment.getUpdatedAt(),
                comment.isEdited());
    }
}
