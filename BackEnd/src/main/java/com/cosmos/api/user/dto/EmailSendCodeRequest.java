package com.cosmos.api.user.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** 인증 코드 발송 요청. 가입 요청과 같은 이메일 규칙을 쓴다. */
public record EmailSendCodeRequest(

        @NotBlank(message = "이메일을 입력해 주세요.")
        @Email(message = "올바른 형식의 이메일 주소여야 합니다.")
        @Size(max = 255, message = "이메일은 255자 이하여야 합니다.")
        String email
) {
}
