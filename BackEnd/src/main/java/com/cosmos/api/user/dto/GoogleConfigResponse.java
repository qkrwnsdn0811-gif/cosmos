package com.cosmos.api.user.dto;

/** 프론트가 Google 로그인 버튼을 그릴지와 그때 쓸 클라이언트 ID. 클라이언트 ID 는 공개값이다. */
public record GoogleConfigResponse(boolean enabled, String clientId) {
}
