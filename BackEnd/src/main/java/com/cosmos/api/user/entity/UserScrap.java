package com.cosmos.api.user.entity;

import jakarta.persistence.Column;
import jakarta.persistence.EmbeddedId;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.FetchType;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.MapsId;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

/**
 * 사용자가 스크랩한 뉴스 한 건. "누가(user) 어떤 뉴스를(documentId) 언제(createdAt)"만 기록한다.
 * 뉴스 쪽은 JPA 엔티티 없이 SQL로 조회하는 구조라, 뉴스 번호(UUID)만 값으로 들고 있다.
 */
@Entity
@Table(name = "user_scrap")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@EntityListeners(AuditingEntityListener.class)
public class UserScrap {

    @EmbeddedId
    private UserScrapId id;

    @MapsId("userId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    private UserScrap(User user, UUID documentId) {
        this.id = new UserScrapId(user.getUserId(), documentId);
        this.user = user;
    }

    public static UserScrap create(User user, UUID documentId) {
        return new UserScrap(user, documentId);
    }
}
