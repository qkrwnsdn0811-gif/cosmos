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
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 토큰 재발급 API 테스트.
 * Access Token이 만료됐을 때 프론트가 조용히 이어서 쓰게 해주는 통로이므로,
 * 정상 재발급뿐 아니라 "거부해야 하는 경우"를 꼼꼼히 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // DB는 자동 롤백. Redis는 아래 @AfterEach로 직접 정리한다
class TokenRefreshControllerTest {

    private static final String EMAIL = "refresh@test.com";
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
    private String refreshToken;

    /** 가입 → 로그인까지 마친 상태에서 각 테스트를 시작한다. */
    @BeforeEach
    void loginFirst() throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new SignupRequest(EMAIL, PASSWORD, "재발급테스트"))))
                .andExpect(status().isCreated());

        MvcResult result = mockMvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new LoginRequest(EMAIL, PASSWORD))))
                .andExpect(status().isOk())
                .andReturn();

        userId = userRepository.findByEmail(EMAIL).orElseThrow().getUserId();
        refreshToken = result.getResponse().getCookie(RefreshTokenCookie.NAME).getValue();
    }

    @AfterEach
    void cleanRedis() {
        refreshTokenStore.delete(userId);
    }

    private ResultActions refreshWith(String token) throws Exception {
        return mockMvc.perform(post("/api/auth/refresh")
                .cookie(new Cookie(RefreshTokenCookie.NAME, token)));
    }

    // 상황: 로그인해서 받은 쿠키로 재발급 요청
    // 기대: 200 + 새 Access Token, 그 토큰에 원래 사용자 번호가 들어 있음
    @Test
    @DisplayName("유효한 Refresh 쿠키로 새 Access Token을 받는다")
    void reissue_success() throws Exception {
        MvcResult result = refreshWith(refreshToken)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accessToken").isString())
                .andReturn();

        String accessToken = objectMapper.readTree(result.getResponse().getContentAsString())
                .get("accessToken").asString();
        assertThat(jwtTokenProvider.parseAccessToken(accessToken).userId()).isEqualTo(userId);
    }

    // 상황: 쿠키 없이 재발급 요청 (로그인한 적 없는 브라우저)
    // 기대: 401 TOKEN_INVALID → 로그인 화면으로
    @Test
    @DisplayName("쿠키가 없으면 401 TOKEN_INVALID")
    void no_cookie() throws Exception {
        mockMvc.perform(post("/api/auth/refresh"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 아무 문자열이나 쿠키에 담아 요청
    // 기대: 401 TOKEN_INVALID
    @Test
    @DisplayName("형식이 잘못된 토큰이면 401 TOKEN_INVALID")
    void malformed_token() throws Exception {
        refreshWith("garbage")
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 서버(Redis)에서 토큰을 지운 뒤(=로그아웃한 상태) 예전 쿠키로 요청
    // 기대: 401 TOKEN_INVALID — 쿠키가 남아 있어도 서버가 폐기했으면 통하지 않는다
    @Test
    @DisplayName("서버에서 폐기된 토큰은 쿠키가 남아 있어도 거부된다")
    void revoked_token() throws Exception {
        refreshTokenStore.delete(userId);

        refreshWith(refreshToken)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 다른 기기에서 다시 로그인해 서버 토큰이 교체된 뒤, 예전 쿠키로 요청
    // 기대: 401 TOKEN_INVALID — 서버 사본과 다르므로 거부
    @Test
    @DisplayName("다시 로그인해 교체된 뒤에는 예전 토큰이 거부된다")
    void superseded_token() throws Exception {
        mockMvc.perform(post("/api/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new LoginRequest(EMAIL, PASSWORD))))
                .andExpect(status().isOk());

        refreshWith(refreshToken)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 유효기간이 지난 Refresh Token (과거 시계로 만들어 재현)
    // 기대: 401 TOKEN_EXPIRED → 프론트는 재시도하지 않고 로그인 화면으로
    @Test
    @DisplayName("만료된 Refresh Token이면 401 TOKEN_EXPIRED")
    void expired_token() throws Exception {
        Clock longAgo = Clock.fixed(Instant.now().minus(Duration.ofDays(30)), ZoneOffset.UTC);
        String expired = new JwtTokenProvider(jwtProperties, longAgo).createRefreshToken(userId);
        refreshTokenStore.save(userId, expired); // 서버 사본도 같게 맞춰 만료 자체를 검증

        refreshWith(expired)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_EXPIRED"));
    }

    // 상황: Access Token을 Refresh 쿠키 자리에 넣어 요청
    // 기대: 401 TOKEN_INVALID — 용도가 다른 토큰은 재발급에 쓸 수 없다
    @Test
    @DisplayName("Access Token으로는 재발급받을 수 없다")
    void access_token_cannot_refresh() throws Exception {
        String accessToken = jwtTokenProvider.createAccessToken(
                userId, userRepository.findById(userId).orElseThrow().getRole());

        refreshWith(accessToken)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }
}
