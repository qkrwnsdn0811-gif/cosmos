package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtProperties;
import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.global.security.RefreshTokenCookie;
import com.cosmos.api.global.security.RefreshTokenStore;
import com.cosmos.api.user.dto.LoginRequest;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.Role;
import com.cosmos.api.user.repository.UserRepository;
import jakarta.servlet.http.Cookie;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 로그아웃 API 테스트.
 * 로그아웃의 핵심은 "서버가 보관한 Refresh Token을 지워 더는 재발급되지 않게 하는 것"이므로,
 * 응답 코드뿐 아니라 로그아웃 뒤 재발급이 실제로 막히는지까지 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // DB는 자동 롤백. Redis는 아래 @AfterEach로 직접 정리한다
class LogoutControllerTest {

    private static final String EMAIL = "logout@test.com";
    private static final String PASSWORD = "password1";

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    UserRepository userRepository;

    @Autowired
    RefreshTokenStore refreshTokenStore;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    @Autowired
    JwtProperties jwtProperties;

    private Long userId;
    private String accessToken;
    private String refreshToken;

    /** 가입 → 로그인까지 마친 상태에서 각 테스트를 시작한다. */
    @BeforeEach
    void loginFirst() throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new SignupRequest(EMAIL, PASSWORD, "로그아웃테스트"))))
                .andExpect(status().isCreated());

        MvcResult result = mockMvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new LoginRequest(EMAIL, PASSWORD))))
                .andExpect(status().isOk())
                .andReturn();

        userId = userRepository.findByEmail(EMAIL).orElseThrow().getUserId();
        accessToken = objectMapper.readTree(result.getResponse().getContentAsString())
                .get("accessToken").asString();
        refreshToken = result.getResponse().getCookie(RefreshTokenCookie.NAME).getValue();
    }

    @AfterEach
    void cleanRedis() {
        refreshTokenStore.delete(userId);
    }

    private ResultActions logoutWith(String token) throws Exception {
        return mockMvc.perform(post("/api/auth/logout")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token));
    }

    // 상황: 로그인 상태에서 로그아웃 요청
    // 기대: 204(본문 없음) + 브라우저 쿠키를 지우는 Set-Cookie(Max-Age=0)
    @Test
    @DisplayName("로그아웃하면 204와 함께 쿠키를 지우는 응답을 준다")
    void logout_success() throws Exception {
        MvcResult result = logoutWith(accessToken)
                .andExpect(status().isNoContent())
                .andReturn();

        String setCookie = result.getResponse().getHeader(HttpHeaders.SET_COOKIE);
        assertThat(setCookie).startsWith("refresh_token=");
        assertThat(setCookie).contains("Max-Age=0");
        assertThat(setCookie).contains("Path=/api/auth");
    }

    // 상황: 로그아웃 후 Redis 확인
    // 기대: 보관하던 Refresh Token이 사라짐
    @Test
    @DisplayName("로그아웃하면 서버에 보관된 Refresh Token이 삭제된다")
    void logout_clears_stored_token() throws Exception {
        assertThat(refreshTokenStore.find(userId)).isPresent();

        logoutWith(accessToken).andExpect(status().isNoContent());

        assertThat(refreshTokenStore.find(userId)).isEmpty();
    }

    // 상황: 로그아웃한 뒤, 남아 있던 쿠키로 재발급 시도
    // 기대: 401 TOKEN_INVALID — 로그아웃이 실제로 효력을 갖는다
    @Test
    @DisplayName("로그아웃 후에는 예전 쿠키로 재발급받을 수 없다")
    void refresh_blocked_after_logout() throws Exception {
        logoutWith(accessToken).andExpect(status().isNoContent());

        mockMvc.perform(post("/api/auth/refresh")
                        .cookie(new Cookie(RefreshTokenCookie.NAME, refreshToken)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 토큰 없이 로그아웃 요청
    // 기대: 401 TOKEN_INVALID (누구를 로그아웃시킬지 알 수 없음)
    @Test
    @DisplayName("토큰 없이 로그아웃하면 401 TOKEN_INVALID")
    void logout_without_token() throws Exception {
        mockMvc.perform(post("/api/auth/logout"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 만료된 Access Token으로 로그아웃 요청
    // 기대: 401 TOKEN_EXPIRED — 프론트는 재발급 후 다시 시도해야 한다
    @Test
    @DisplayName("만료된 토큰으로 로그아웃하면 401 TOKEN_EXPIRED")
    void logout_with_expired_token() throws Exception {
        Clock longAgo = Clock.fixed(Instant.now().minus(Duration.ofDays(1)), ZoneOffset.UTC);
        String expired = new JwtTokenProvider(jwtProperties, longAgo).createAccessToken(userId, Role.ROLE_USER);

        logoutWith(expired)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_EXPIRED"));
    }

    // 상황: 이미 로그아웃한 상태에서 같은 토큰으로 한 번 더 로그아웃
    // 기대: 204 — 이미 지워진 상태여도 오류를 내지 않는다 (프론트가 재시도해도 안전)
    @Test
    @DisplayName("두 번 로그아웃해도 오류 없이 204를 준다")
    void logout_twice_is_safe() throws Exception {
        logoutWith(accessToken).andExpect(status().isNoContent());

        logoutWith(accessToken).andExpect(status().isNoContent());
    }
}
