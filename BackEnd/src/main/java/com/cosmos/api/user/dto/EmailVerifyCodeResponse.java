package com.cosmos.api.user.dto;

/**
 * 인증 코드 확인 응답.
 *
 * @param email             인증된 주소 (소문자로 정규화된 값)
 * @param verified          항상 true (실패는 오류 응답으로 내려간다)
 * @param validForSeconds   인증 완료 상태가 유지되는 시간(초). 이 안에 가입을 마쳐야 한다
 */
public record EmailVerifyCodeResponse(
        String email,
        boolean verified,
        long validForSeconds
) {
}
