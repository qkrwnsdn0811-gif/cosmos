package com.cosmos.api.user.dto;

/** 중복 확인 API의 응답. 쓸 수 있으면 true, 누가 이미 쓰고 있으면 false. */
public record AvailabilityResponse(
        boolean available
) {
}
