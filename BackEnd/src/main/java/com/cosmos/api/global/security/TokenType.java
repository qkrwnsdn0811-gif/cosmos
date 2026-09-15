package com.cosmos.api.global.security;

/** 토큰 용도. Refresh Token으로 API를 호출하는 것을 막기 위해 토큰 안에 함께 기록한다. */
public enum TokenType {
    ACCESS,
    REFRESH
}
