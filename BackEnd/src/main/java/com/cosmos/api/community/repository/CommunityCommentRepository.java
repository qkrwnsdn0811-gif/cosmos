package com.cosmos.api.community.repository;

import com.cosmos.api.community.entity.CommunityComment;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * 댓글 목록은 최신순(작성 시각 내림차순, 같으면 댓글 번호 내림차순)으로 읽는다.
 * 첫 페이지와 다음 페이지 조회를 나눈 이유는, 커서가 없을 때 비교 조건을 아예 빼기 위해서다.
 *
 * <p>작성자·기업 정보를 화면에서 함께 보여주므로 join fetch로 한 번에 읽어 온다.
 */
public interface CommunityCommentRepository extends JpaRepository<CommunityComment, UUID> {

    @Query("""
            select c from CommunityComment c
            join fetch c.user
            join fetch c.company co
            where co.companyId = :companyId
            order by c.createdAt desc, c.commentId desc
            """)
    List<CommunityComment> findFirstPageByCompany(@Param("companyId") UUID companyId, Pageable pageable);

    @Query("""
            select c from CommunityComment c
            join fetch c.user
            join fetch c.company co
            where co.companyId = :companyId
              and (c.createdAt < :createdAt
                   or (c.createdAt = :createdAt and c.commentId < :commentId))
            order by c.createdAt desc, c.commentId desc
            """)
    List<CommunityComment> findNextPageByCompany(
            @Param("companyId") UUID companyId,
            @Param("createdAt") Instant createdAt,
            @Param("commentId") UUID commentId,
            Pageable pageable);

    @Query("""
            select c from CommunityComment c
            join fetch c.user
            join fetch c.company
            order by c.createdAt desc, c.commentId desc
            """)
    List<CommunityComment> findFirstPage(Pageable pageable);

    @Query("""
            select c from CommunityComment c
            join fetch c.user
            join fetch c.company
            where c.createdAt < :createdAt
               or (c.createdAt = :createdAt and c.commentId < :commentId)
            order by c.createdAt desc, c.commentId desc
            """)
    List<CommunityComment> findNextPage(
            @Param("createdAt") Instant createdAt,
            @Param("commentId") UUID commentId,
            Pageable pageable);
}
