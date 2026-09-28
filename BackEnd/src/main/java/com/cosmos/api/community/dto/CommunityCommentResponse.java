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

    /**
     * 어느 기업 글인지 알려주는 최소 정보.
     * 종목코드·시장은 프론트가 기업 로고 파일을 찾는 열쇠라 함께 준다 (없는 기업은 null → 화면은 이니셜로 표시).
     */
    public record CompanyBrief(UUID companyId, String name, String stockCode, String market) {

        public static CompanyBrief from(Company company) {
            return new CompanyBrief(
                    company.getCompanyId(), company.getName(), company.getStockCode(), company.getMarket());
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
