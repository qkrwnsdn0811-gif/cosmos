package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.global.security.RefreshTokenStore;
import com.cosmos.api.user.dto.LoginRequest;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.repository.UserRepository;
import org.junit.jupiter.api.AfterEach;
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
 * 로그인 API 테스트.
 * 토큰 두 개가 약속한 자리(본문 / httpOnly 쿠키)로 나가는지, Refresh가 Redis에 보관되는지,
 * 실패 시 가입 여부가 드러나지 않는지, 프론트 주소에서 쿠키 포함 호출(CORS)이 허용되는지 검증한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // DB 변경은 자동으로 되돌아간다. Redis는 그렇지 않아서 아래 @AfterEach로 직접 지운다
class LoginControllerTest {

    private static final String EMAIL = "login@test.com";
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

    @AfterEach
    void cleanRedis() {
        userRepository.findByEmail(EMAIL).ifPresent(user -> refreshTokenStore.delete(user.getUserId()));
    }

    private void signup() throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new SignupRequest(EMAIL, PASSWORD, "로그인테스트"))))
                .andExpect(status().isCreated());
    }

    private ResultActions login(String email, String password) throws Exception {
        return mockMvc.perform(post("/api/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new LoginRequest(email, password))));
    }

    // 상황: 가입한 계정으로 로그인
    // 기대: 200 + 본문에 accessToken, 쿠키에 refresh_token(httpOnly, Path=/api/auth)
    @Test
    @DisplayName("로그인 성공 시 Access는 본문, Refresh는 httpOnly 쿠키로 나간다")
    void login_success() throws Exception {
        signup();

        MvcResult result = login(EMAIL, PASSWORD)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accessToken").isString())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE))
                .andReturn();

        String setCookie = result.getResponse().getHeader(HttpHeaders.SET_COOKIE);
        assertThat(setCookie).startsWith("refresh_token=");
        assertThat(setCookie).contains("HttpOnly");
        assertThat(setCookie).contains("Path=/api/auth");
        assertThat(setCookie).contains("SameSite=Lax");
    }

    // 상황: 로그인 후 발급된 Access Token을 해석
    // 기대: 토큰 안의 사용자 번호가 실제 가입한 회원의 번호와 같다
    @Test
    @DisplayName("발급된 Access Token에는 로그인한 회원의 번호가 들어 있다")
    void access_token_contains_user_id() throws Exception {
        signup();
        Long userId = userRepository.findByEmail(EMAIL).orElseThrow().getUserId();

        MvcResult result = login(EMAIL, PASSWORD).andExpect(status().isOk()).andReturn();
        String accessToken = objectMapper.readTree(result.getResponse().getContentAsString())
                .get("accessToken").asString();

        assertThat(jwtTokenProvider.parseAccessToken(accessToken).userId()).isEqualTo(userId);
    }

    // 상황: 로그인 직후 Redis 확인
    // 기대: "refresh:{userId}" 키에 쿠키로 나간 것과 같은 Refresh Token이 저장돼 있다
    @Test
    @DisplayName("Refresh Token은 Redis에 보관되어 나중에 대조·폐기할 수 있다")
    void refresh_token_stored_in_redis() throws Exception {
        signup();
        Long userId = userRepository.findByEmail(EMAIL).orElseThrow().getUserId();

        MvcResult result = login(EMAIL, PASSWORD).andExpect(status().isOk()).andReturn();
        String cookieValue = result.getResponse().getCookie("refresh_token").getValue();

        assertThat(refreshTokenStore.find(userId)).contains(cookieValue);
        assertThat(jwtTokenProvider.parseRefreshToken(cookieValue)).isEqualTo(userId);
    }

    // 상황: 대문자를 섞은 이메일로 로그인
    // 기대: 가입 때 소문자로 저장했어도 같은 계정으로 인식해 200
    @Test
    @DisplayName("이메일은 대소문자를 구분하지 않고 로그인된다")
    void login_email_case_insensitive() throws Exception {
        signup();

        login("LOGIN@Test.com", PASSWORD).andExpect(status().isOk());
    }

    // 상황: 비밀번호를 틀리게 입력
    // 기대: 401 + INVALID_CREDENTIALS
    @Test
    @DisplayName("비밀번호가 틀리면 401 INVALID_CREDENTIALS")
    void login_wrong_password() throws Exception {
        signup();

        login(EMAIL, "wrongpass1")
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"));
    }

    // 상황: 가입하지 않은 이메일로 로그인
    // 기대: 비밀번호 틀린 경우와 똑같은 401 INVALID_CREDENTIALS (가입 여부를 알려주지 않음)
    @Test
    @DisplayName("없는 이메일도 비밀번호 오류와 같은 응답을 낸다")
    void login_unknown_email_same_response() throws Exception {
        login("nobody@test.com", PASSWORD)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"));
    }

    // 상황: 비밀번호를 비워서 요청
    // 기대: 400 + VALIDATION_FAILED, fieldErrors에 password
    @Test
    @DisplayName("입력값이 비어 있으면 400 VALIDATION_FAILED")
    void login_validation_failed() throws Exception {
        login(EMAIL, "")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("password"));
    }

    // 상황: 프론트(localhost:5173)가 브라우저 사전 확인(OPTIONS) 요청을 보냄
    // 기대: 해당 주소 허용 + 쿠키 포함(credentials) 허용 응답
    @Test
    @DisplayName("프론트 주소에서 쿠키를 포함한 호출이 CORS로 허용된다")
    void cors_allows_frontend_with_credentials() throws Exception {
        mockMvc.perform(options("/api/auth/login")
                        .header(HttpHeaders.ORIGIN, "http://localhost:5173")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "POST"))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ACCESS_CONTROL_ALLOW_ORIGIN, "http://localhost:5173"))
                .andExpect(header().string(HttpHeaders.ACCESS_CONTROL_ALLOW_CREDENTIALS, "true"));
    }

    // 상황: 허용 목록에 없는 주소에서 사전 확인 요청
    // 기대: 거부 (403)
    @Test
    @DisplayName("허용하지 않은 주소의 호출은 CORS로 거부된다")
    void cors_rejects_unknown_origin() throws Exception {
        mockMvc.perform(options("/api/auth/login")
                        .header(HttpHeaders.ORIGIN, "http://evil.example.com")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "POST"))
                .andExpect(status().isForbidden());
    }
}
