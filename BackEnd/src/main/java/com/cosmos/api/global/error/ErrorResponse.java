package com.cosmos.api.global.error;

import java.util.List;

public record ErrorResponse(
        String code,
        String message,
        List<FieldErrorResponse> fieldErrors
) {

    public ErrorResponse {
        fieldErrors = List.copyOf(fieldErrors);
    }

    public static ErrorResponse from(ErrorCode errorCode) {
        return new ErrorResponse(errorCode.code(), errorCode.message(), List.of());
    }

    public static ErrorResponse validation(List<FieldErrorResponse> fieldErrors) {
        CommonErrorCode errorCode = CommonErrorCode.VALIDATION_FAILED;
        return new ErrorResponse(errorCode.code(), errorCode.message(), fieldErrors);
    }
}
