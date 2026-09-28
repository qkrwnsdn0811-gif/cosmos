package com.cosmos.api.community.entity;

import com.cosmos.api.company.entity.Company;
import com.cosmos.api.user.entity.User;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.FetchType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

/**
 * 기업 커뮤니티 댓글. 대댓글·좋아요 없이 기업과 작성자에 바로 연결되는 한 단계 구조다.
 * 삭제는 soft delete가 아니라 실제 삭제(요구사항 기준).
 */
@Entity
@Table(name = "company_community_comment")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@EntityListeners(AuditingEntityListener.class)
public class CommunityComment {

    @Id
    @Column(name = "comment_id", nullable = false)
    private UUID commentId;

    // 목록 조회에서만 기업·작성자 정보를 쓰므로 필요할 때 읽어 오도록 LAZY로 둔다
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "company_id", nullable = false)
    private Company company;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    private CommunityComment(Company company, User user, String content) {
        this.commentId = UUID.randomUUID();
        this.company = company;
        this.user = user;
        this.content = content;
    }

    public static CommunityComment create(Company company, User user, String content) {
        return new CommunityComment(company, user, content);
    }

    public void changeContent(String content) {
        this.content = content;
    }

    /** 이 댓글을 쓴 사람인지. 수정·삭제 전에 확인한다. */
    public boolean isWrittenBy(Long userId) {
        return user.getUserId().equals(userId);
    }

    /** 작성 후 한 번이라도 수정됐는지. 화면에 "수정됨" 표시를 위해 쓴다. */
    public boolean isEdited() {
        return updatedAt != null && createdAt != null && updatedAt.isAfter(createdAt);
    }
}
