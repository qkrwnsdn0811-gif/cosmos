package com.cosmos.api.community.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 댓글 작성·수정 요청. 작성과 수정의 입력 규칙이 같아 하나를 함께 쓴다. */
public record CommentRequest(

        @NotBlank(message = "댓글 내용을 입력해 주세요.")
        @Size(max = 500, message = "댓글은 500자 이하여야 합니다.")
        String content
) {

    /** 앞뒤 공백을 제거한 내용. "   " 같은 공백만 있는 입력은 @NotBlank가 먼저 걸러낸다. */
    public String trimmedContent() {
        return content.trim();
    }
}
