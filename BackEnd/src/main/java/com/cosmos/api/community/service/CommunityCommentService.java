package com.cosmos.api.community.service;

import com.cosmos.api.community.dto.CommentResponse;
import com.cosmos.api.community.dto.CommunityCommentResponse;
import com.cosmos.api.community.dto.CompanyCommentResponse;
import com.cosmos.api.community.entity.CommunityComment;
import com.cosmos.api.community.error.CommunityErrorCode;
import com.cosmos.api.community.repository.CommunityCommentRepository;
import com.cosmos.api.company.entity.Company;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyRepository;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.repository.UserRepository;
import java.util.List;
import java.util.UUID;
import java.util.function.Function;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 기업 커뮤니티 댓글 작성·수정·삭제. 수정과 삭제는 본인 댓글만 가능하다. */
@Service
@RequiredArgsConstructor
public class CommunityCommentService {

    private final CommunityCommentRepository commentRepository;
    private final CompanyRepository companyRepository;
    private final UserRepository userRepository;
    private final TimeIdCursorCodec cursorCodec;

    /** 기업별 댓글 목록 (최신순). 비로그인도 볼 수 있다. */
    @Transactional(readOnly = true)
    public CursorPageResponse<CompanyCommentResponse> findByCompany(UUID companyId, String cursor, int size) {
        if (companyRepository.findActiveById(companyId).isEmpty()) {
            throw new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND);
        }

        TimeIdCursor from = cursorCodec.decode(cursor);
        Pageable limit = pageOf(size);
        List<CommunityComment> rows = (from == null)
                ? commentRepository.findFirstPageByCompany(companyId, limit)
                : commentRepository.findNextPageByCompany(companyId, from.at(), from.id(), limit);

        return toPage(rows, size, CompanyCommentResponse::from);
    }

    /** 전체 커뮤니티 댓글 목록 (여러 기업 통합, 최신순). 비로그인도 볼 수 있다. */
    @Transactional(readOnly = true)
    public CursorPageResponse<CommunityCommentResponse> findAll(String cursor, int size) {
        TimeIdCursor from = cursorCodec.decode(cursor);
        Pageable limit = pageOf(size);
        List<CommunityComment> rows = (from == null)
                ? commentRepository.findFirstPage(limit)
                : commentRepository.findNextPage(from.at(), from.id(), limit);

        return toPage(rows, size, CommunityCommentResponse::from);
    }

    // 다음 페이지가 있는지 알려면 요청보다 1개 더 읽어 본다
    private Pageable pageOf(int size) {
        return PageRequest.of(0, size + 1);
    }

    /**
     * 1개 더 읽어 온 결과를 페이지 응답으로 다듬는다.
     * 여분이 있으면 잘라내고 hasNext=true, 마지막 항목으로 다음 커서를 만든다.
     */
    private <T> CursorPageResponse<T> toPage(
            List<CommunityComment> rows, int size, Function<CommunityComment, T> mapper) {

        boolean hasNext = rows.size() > size;
        List<CommunityComment> pageRows = hasNext ? rows.subList(0, size) : rows;

        String nextCursor = null;
        if (hasNext) {
            CommunityComment last = pageRows.get(pageRows.size() - 1);
            nextCursor = cursorCodec.encode(new TimeIdCursor(last.getCreatedAt(), last.getCommentId()));
        }
        return CursorPageResponse.of(pageRows.stream().map(mapper).toList(), nextCursor, hasNext);
    }

    @Transactional
    public CommentResponse create(Long userId, UUID companyId, String content) {
        Company company = companyRepository.findActiveById(companyId)
                .orElseThrow(() -> new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND));
        User user = findActiveUser(userId);

        CommunityComment comment = CommunityComment.create(company, user, content);
        return CommentResponse.from(commentRepository.save(comment));
    }

    @Transactional
    public CommentResponse update(Long userId, UUID commentId, String content) {
        CommunityComment comment = findOwnComment(userId, commentId);
        comment.changeContent(content);
        // 수정 시각(updatedAt)은 DB 반영 시점에 채워지므로, 응답에 담기 전에 먼저 반영시킨다
        commentRepository.flush();
        return CommentResponse.from(comment);
    }

    @Transactional
    public void delete(Long userId, UUID commentId) {
        commentRepository.delete(findOwnComment(userId, commentId));
    }

    /**
     * 댓글을 찾고 "내가 쓴 것인지"까지 확인한다.
     * 없으면 404, 남의 댓글이면 403으로 구분해 알려준다.
     */
    private CommunityComment findOwnComment(Long userId, UUID commentId) {
        CommunityComment comment = commentRepository.findById(commentId)
                .orElseThrow(() -> new BusinessException(CommunityErrorCode.COMMENT_NOT_FOUND));

        if (!comment.isWrittenBy(userId)) {
            throw new BusinessException(CommunityErrorCode.COMMENT_FORBIDDEN);
        }
        return comment;
    }

    // 토큰은 30분간 살아 있어서, 그 사이 탈퇴한 회원의 토큰이 들어올 수 있다
    private User findActiveUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(AuthErrorCode.TOKEN_INVALID));
    }
}
