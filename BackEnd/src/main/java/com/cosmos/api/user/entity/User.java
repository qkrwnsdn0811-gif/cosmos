package com.cosmos.api.user.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EntityListeners;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.SQLDelete;
import org.hibernate.annotations.SQLRestriction;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

@Entity
@Table(name = "users")
@SQLDelete(sql = "UPDATE users SET deleted_at = now() WHERE user_id = ?") // delete() 호출 시 soft delete
@SQLRestriction("deleted_at IS NULL")                                // 모든 조회에서 탈퇴 회원 자동 제외
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@EntityListeners(AuditingEntityListener.class)
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "user_id")
    private Long userId;

    @Column(nullable = false)
    private String email;

    /** Google 로만 가입한 회원은 null (비밀번호 로그인 불가). */
    private String password;

    /** Google 계정 고유 ID(ID 토큰의 sub). Google 로그인을 쓴 적 없으면 null. */
    private String googleSub;

    @Column(nullable = false, length = 30)
    private String nickname;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private Role role;

    private Instant emailVerifiedAt;

    @CreatedDate
    @Column(nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(nullable = false)
    private Instant updatedAt;

    private Instant deletedAt;

    private User(String email, String password, String nickname, Role role) {
        this.email = email;
        this.password = password;
        this.nickname = nickname;
        this.role = role;
    }

    /**
     * @param encodedPassword 반드시 인코딩(BCrypt)된 값. 평문을 넘기면 안 된다.
     */
    public static User create(String email, String encodedPassword, String nickname) {
        // ux_users_email 인덱스가 lower(email) 기준이므로 저장 전 소문자 정규화
        return new User(email.toLowerCase(), encodedPassword, nickname, Role.ROLE_USER);
    }

    /** Google 로 처음 로그인한 사람을 비밀번호 없이 가입시킨다. Google 이 확인한 이메일이므로 인증 완료로 둔다. */
    public static User createWithGoogle(String email, String nickname, String googleSub) {
        User user = new User(email.toLowerCase(), null, nickname, Role.ROLE_USER);
        user.googleSub = googleSub;
        user.emailVerifiedAt = Instant.now();
        return user;
    }

    /** 이메일로 가입한 회원이 같은 이메일의 Google 계정으로 로그인하면 그 계정을 연결한다. */
    public void linkGoogle(String googleSub) {
        this.googleSub = googleSub;
        if (this.emailVerifiedAt == null) {
            this.emailVerifiedAt = Instant.now();
        }
    }

    public void verifyEmail() {
        this.emailVerifiedAt = Instant.now();
    }

    public void changeNickname(String nickname) {
        this.nickname = nickname;
    }

    public void changePassword(String encodedPassword) {
        this.password = encodedPassword;
    }
}
