package com.cosmos.api.community.controller;

import com.cosmos.api.community.dto.CommentRequest;
import com.cosmos.api.community.dto.CommentResponse;
import com.cosmos.api.community.dto.CommunityCommentResponse;
import com.cosmos.api.community.dto.CompanyCommentResponse;
import com.cosmos.api.community.service.CommunityCommentService;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthUser;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 커뮤니티 댓글 API. 목록 조회는 공개, 작성·수정·삭제는 로그인이 필요하다. */
@RestController
@RequiredArgsConstructor
@Validated // 쿼리 파라미터(size) 검증 활성화
public class CommunityCommentController {

    private final CommunityCommentService commentService;

    /** 기업별 댓글 목록 (최신순). 로그인 없이도 볼 수 있다. */
    @GetMapping("/api/companies/{companyId}/comments")
    public CursorPageResponse<CompanyCommentResponse> findByCompany(
            @PathVariable UUID companyId,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return commentService.findByCompany(companyId, cursor, size);
    }

    /** 전체 커뮤니티 댓글 목록 (기업 구분 없이 최신순). 로그인 없이도 볼 수 있다. */
    @GetMapping("/api/community/comments")
    public CursorPageResponse<CommunityCommentResponse> findAll(
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return commentService.findAll(cursor, size);
    }

    /** 기업 커뮤니티에 댓글 작성. */
    @PostMapping("/api/companies/{companyId}/comments")
    public ResponseEntity<CommentResponse> create(
            @AuthenticationPrincipal AuthUser authUser,
            @PathVariable UUID companyId,
            @Valid @RequestBody CommentRequest request
    ) {
        CommentResponse response = commentService.create(authUser.userId(), companyId, request.trimmedContent());
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /** 내 댓글 수정. 남의 댓글이면 403. */
    @PatchMapping("/api/comments/{commentId}")
    public CommentResponse update(
            @AuthenticationPrincipal AuthUser authUser,
            @PathVariable UUID commentId,
            @Valid @RequestBody CommentRequest request
    ) {
        return commentService.update(authUser.userId(), commentId, request.trimmedContent());
    }

    /** 내 댓글 삭제. 남의 댓글이면 403. */
    @DeleteMapping("/api/comments/{commentId}")
    public ResponseEntity<Void> delete(
            @AuthenticationPrincipal AuthUser authUser,
            @PathVariable UUID commentId
    ) {
        commentService.delete(authUser.userId(), commentId);
        return ResponseEntity.noContent().build();
    }
}
