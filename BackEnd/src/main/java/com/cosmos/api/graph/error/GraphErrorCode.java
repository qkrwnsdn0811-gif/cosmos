package com.cosmos.api.graph.error;

import com.cosmos.api.global.error.ErrorCode;
import org.springframework.http.HttpStatus;

public enum GraphErrorCode implements ErrorCode {

    GRAPH_SNAPSHOT_NOT_FOUND(HttpStatus.NOT_FOUND, "공개된 관계 그래프를 찾을 수 없습니다."),
    RELATIONSHIP_NOT_FOUND(HttpStatus.NOT_FOUND, "기업 관계를 찾을 수 없습니다.");

    private final HttpStatus status;
    private final String message;

    GraphErrorCode(HttpStatus status, String message) {
        this.status = status;
        this.message = message;
    }

    @Override
    public HttpStatus status() {
        return status;
    }

    @Override
    public String code() {
        return name();
    }

    @Override
    public String message() {
        return message;
    }
}
