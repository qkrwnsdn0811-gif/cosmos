package com.cosmos.api.user.entity;

import com.cosmos.api.company.entity.Company;
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
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

/**
 * 사용자가 등록한 관심 기업 한 건.
 * "누가(user) 어떤 기업을(company) 언제(createdAt) 등록했나"만 기록하는 단순한 연결 테이블이다.
 */
@Entity
@Table(name = "user_watch_company")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@EntityListeners(AuditingEntityListener.class)
public class UserWatchCompany {

    @EmbeddedId
    private UserWatchCompanyId id;

    @MapsId("userId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @MapsId("companyId")
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "company_id", nullable = false)
    private Company company;

    @CreatedDate
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    private UserWatchCompany(User user, Company company) {
        this.id = new UserWatchCompanyId(user.getUserId(), company.getCompanyId());
        this.user = user;
        this.company = company;
    }

    public static UserWatchCompany create(User user, Company company) {
        return new UserWatchCompany(user, company);
    }
}
