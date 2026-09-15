package com.cosmos.api.global.cursor;

import java.time.Instant;
import java.util.UUID;

/**
 * "시각 + 번호"로 정렬되는 목록의 "여기까지 봤다" 표시.
 * 시각이 같은 항목이 있을 수 있어 번호(UUID)까지 함께 기억한다.
 * 댓글 목록(작성 시각 + 댓글 번호), 관심 기업 목록(등록 시각 + 기업 번호) 등이 공통으로 쓴다.
 */
public record TimeIdCursor(
        Instant at,
        UUID id
) {
}
