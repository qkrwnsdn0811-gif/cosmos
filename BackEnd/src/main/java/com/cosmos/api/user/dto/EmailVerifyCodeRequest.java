package com.cosmos.api.user.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** 인증 코드 확인 요청. 코드는 메일로 받은 숫자 6자리. */
public record EmailVerifyCodeRequest(

        @NotBlank(message = "이메일을 입력해 주세요.")
        @Email(message = "올바른 형식의 이메일 주소여야 합니다.")
        @Size(max = 255, message = "이메일은 255자 이하여야 합니다.")
        String email,

        @NotBlank(message = "인증 코드를 입력해 주세요.")
        @Pattern(regexp = "^\\d{6}$", message = "인증 코드는 숫자 6자리여야 합니다.")
        String code
) {
}
