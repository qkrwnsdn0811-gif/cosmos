package com.cosmos.api.global.error;

public record FieldErrorResponse(
        String field,
        String message
) {
}
