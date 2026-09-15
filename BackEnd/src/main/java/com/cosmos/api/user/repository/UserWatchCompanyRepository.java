package com.cosmos.api.user.repository;

import com.cosmos.api.user.entity.UserWatchCompany;
import com.cosmos.api.user.entity.UserWatchCompanyId;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 관심 기업 목록은 등록 최신순(등록 시각 내림차순, 같으면 기업 번호 내림차순)으로 읽는다.
 * 첫 페이지와 다음 페이지 조회를 나눈 이유는, 커서가 없을 때 비교 조건을 아예 빼기 위해서다.
 *
 * <p>화면에 기업 이름·종목 코드를 함께 보여주므로 기업 정보를 join fetch로 한 번에 읽어 온다.
 * 서비스 대상이 아닌(비활성) 기업은 목록에서 제외한다.
 */
public interface UserWatchCompanyRepository extends JpaRepository<UserWatchCompany, UserWatchCompanyId> {

    @Query("""
            select w from UserWatchCompany w
            join fetch w.company c
            where w.id.userId = :userId
              and c.status = 'ACTIVE'
            order by w.createdAt desc, w.id.companyId desc
            """)
    List<UserWatchCompany> findFirstPage(@Param("userId") Long userId, Pageable pageable);

    @Query("""
            select w from UserWatchCompany w
            join fetch w.company c
            where w.id.userId = :userId
              and c.status = 'ACTIVE'
              and (w.createdAt < :createdAt
                   or (w.createdAt = :createdAt and w.id.companyId < :companyId))
            order by w.createdAt desc, w.id.companyId desc
            """)
    List<UserWatchCompany> findNextPage(
            @Param("userId") Long userId,
            @Param("createdAt") Instant createdAt,
            @Param("companyId") UUID companyId,
            Pageable pageable);
}
