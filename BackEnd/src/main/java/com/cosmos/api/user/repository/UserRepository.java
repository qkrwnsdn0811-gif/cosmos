package com.cosmos.api.user.repository;

import com.cosmos.api.user.entity.User;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserRepository extends JpaRepository<User, Long> {

    // User 엔티티의 @SQLRestriction 덕에 탈퇴 회원은 자동으로 제외된다.
    // email은 항상 소문자로 정규화되어 저장되므로, 호출 전에 소문자로 변환해서 넘길 것.
    boolean existsByEmail(String email);

    boolean existsByNickname(String nickname);

    Optional<User> findByEmail(String email);
}
