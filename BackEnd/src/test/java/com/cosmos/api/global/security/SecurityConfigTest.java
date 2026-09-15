package com.cosmos.api.global.security;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.entity.Role;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 어떤 API가 로그인 없이 열려 있고 어떤 API가 막혀 있는지 검증한다.
 * 실제 API 대신 테스트 전용 주소 두 개(공개/보호)를 임시로 만들어 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Import(SecurityConfigTest.ProbeController.class)
class SecurityConfigTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    @Autowired
    JwtProperties jwtProperties;

    /** 테스트 전용 주소. /api/news/** 는 공개, /api/users/** 는 로그인 필요 구역이다. */
    @RestController
    static class ProbeController {

        @GetMapping("/api/news/probe")
        String publicProbe() {
            return "public";
        }

        @GetMapping("/api/users/probe")
        String protectedProbe(@AuthenticationPrincipal AuthUser user) {
            return String.valueOf(user.userId());
        }
    }

    private String bearer(String token) {
        return "Bearer " + token;
    }

    // 상황: 토큰 없이 공개 조회 주소 호출
    // 기대: 200 (비로그인 사용자도 조회 가능)
    @Test
    @DisplayName("공개 조회 API는 토큰 없이 호출할 수 있다")
    void public_api_without_token() throws Exception {
        mockMvc.perform(get("/api/news/probe"))
                .andExpect(status().isOk())
                .andExpect(content().string("public"));
    }

    // 상황: 토큰 없이 로그인 필요 주소 호출
    // 기대: 401 + TOKEN_INVALID → 프론트는 로그인 화면으로
    @Test
    @DisplayName("로그인이 필요한 API는 토큰 없이 호출하면 401 TOKEN_INVALID")
    void protected_api_without_token() throws Exception {
        mockMvc.perform(get("/api/users/probe"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 정상 발급된 Access Token으로 로그인 필요 주소 호출
    // 기대: 200 + 토큰에 담긴 사용자 번호가 컨트롤러까지 전달됨
    @Test
    @DisplayName("정상 토큰이면 로그인이 필요한 API를 호출할 수 있고 사용자 번호가 전달된다")
    void protected_api_with_valid_token() throws Exception {
        String token = jwtTokenProvider.createAccessToken(123L, Role.ROLE_USER);

        mockMvc.perform(get("/api/users/probe").header(HttpHeaders.AUTHORIZATION, bearer(token)))
                .andExpect(status().isOk())
                .andExpect(content().string("123"));
    }

    // 상황: 유효기간이 지난 토큰으로 호출 (과거 시계로 토큰을 만들어 재현)
    // 기대: 401 + TOKEN_EXPIRED → 프론트는 재발급 시도
    @Test
    @DisplayName("만료된 토큰이면 401 TOKEN_EXPIRED")
    void protected_api_with_expired_token() throws Exception {
        Clock longAgo = Clock.fixed(Instant.now().minus(Duration.ofDays(1)), ZoneOffset.UTC);
        String expired = new JwtTokenProvider(jwtProperties, longAgo).createAccessToken(1L, Role.ROLE_USER);

        mockMvc.perform(get("/api/users/probe").header(HttpHeaders.AUTHORIZATION, bearer(expired)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_EXPIRED"));
    }

    // 상황: 아무 문자열이나 토큰인 척 보냄
    // 기대: 401 + TOKEN_INVALID
    @Test
    @DisplayName("형식이 잘못된 토큰이면 401 TOKEN_INVALID")
    void protected_api_with_malformed_token() throws Exception {
        mockMvc.perform(get("/api/users/probe").header(HttpHeaders.AUTHORIZATION, bearer("garbage")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 공개 API에 잘못된 토큰을 붙여 호출
    // 기대: 공개 API라도 토큰이 잘못됐으면 401로 알려준다 (조용히 무시하지 않음)
    @Test
    @DisplayName("공개 API라도 잘못된 토큰을 보내면 401")
    void public_api_with_invalid_token() throws Exception {
        mockMvc.perform(get("/api/news/probe").header(HttpHeaders.AUTHORIZATION, bearer("garbage")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 회원가입 등 인증 관련 주소는 로그인 전에 호출해야 함
    // 기대: 토큰 없이도 막히지 않는다 (401이 아니어야 함)
    @Test
    @DisplayName("인증 API(/api/auth/**)는 토큰 없이 열려 있다")
    void auth_api_is_public() throws Exception {
        mockMvc.perform(get("/api/auth/check-nickname").param("nickname", "보안설정확인"))
                .andExpect(status().isOk());
    }
}
