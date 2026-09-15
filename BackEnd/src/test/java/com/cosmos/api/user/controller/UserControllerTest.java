package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.user.dto.NicknameUpdateRequest;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.Role;
import com.cosmos.api.user.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 내 정보 조회·닉네임 수정 API 테스트.
 * 토큰에 담긴 사용자 번호로 "내" 정보만 다루는지, 닉네임 중복 정책이 지켜지는지 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class UserControllerTest {

    private static final String EMAIL = "me@test.com";
    private static final String NICKNAME = "내정보테스트";

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    UserRepository userRepository;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    private Long userId;
    private String accessToken;

    /** 가입한 회원 하나와 그 회원의 Access Token을 준비한다. */
    @BeforeEach
    void prepareUser() throws Exception {
        signup(EMAIL, NICKNAME);
        userId = userRepository.findByEmail(EMAIL).orElseThrow().getUserId();
        accessToken = jwtTokenProvider.createAccessToken(userId, Role.ROLE_USER);
    }

    private void signup(String email, String nickname) throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new SignupRequest(email, "password1", nickname))))
                .andExpect(status().isCreated());
    }

    private ResultActions patchNickname(String nickname) throws Exception {
        return mockMvc.perform(patch("/api/users/me")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new NicknameUpdateRequest(nickname))));
    }

    // 상황: 로그인 상태에서 내 정보 조회
    // 기대: 200 + 토큰 주인의 번호·이메일·닉네임
    @Test
    @DisplayName("내 정보 조회는 토큰 주인의 정보를 돌려준다")
    void get_me() throws Exception {
        mockMvc.perform(get("/api/users/me")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value(userId))
                .andExpect(jsonPath("$.email").value(EMAIL))
                .andExpect(jsonPath("$.nickname").value(NICKNAME));
    }

    // 상황: 토큰 없이 내 정보 조회
    // 기대: 401 TOKEN_INVALID
    @Test
    @DisplayName("토큰 없이 내 정보를 조회하면 401")
    void get_me_without_token() throws Exception {
        mockMvc.perform(get("/api/users/me"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 아무도 안 쓰는 닉네임으로 변경
    // 기대: 200 + 바뀐 닉네임, DB에도 반영
    @Test
    @DisplayName("닉네임을 바꾸면 200과 바뀐 정보를 돌려주고 DB에도 반영된다")
    void update_nickname() throws Exception {
        patchNickname("새우주탐험가")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value(userId))
                .andExpect(jsonPath("$.nickname").value("새우주탐험가"));

        assertThat(userRepository.findById(userId).orElseThrow().getNickname()).isEqualTo("새우주탐험가");
    }

    // 상황: 다른 회원이 이미 쓰는 닉네임으로 변경 시도
    // 기대: 409 NICKNAME_DUPLICATED
    @Test
    @DisplayName("다른 사람이 쓰는 닉네임으로는 바꿀 수 없다")
    void update_nickname_duplicated() throws Exception {
        signup("other@test.com", "남의닉네임");

        patchNickname("남의닉네임")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NICKNAME_DUPLICATED"));
    }

    // 상황: 지금 쓰고 있는 자기 닉네임 그대로 변경 요청
    // 기대: 200 — 자기 닉네임은 중복으로 보지 않는다
    @Test
    @DisplayName("지금과 같은 닉네임으로 바꾸는 것은 중복이 아니다")
    void update_nickname_same_as_current() throws Exception {
        patchNickname(NICKNAME)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nickname").value(NICKNAME));
    }

    // 상황: 한 글자짜리 닉네임으로 변경 시도
    // 기대: 400 + nickname 필드 오류
    @Test
    @DisplayName("닉네임이 규칙에 맞지 않으면 400")
    void update_nickname_invalid() throws Exception {
        patchNickname("가")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("nickname"));
    }

    // 상황: 탈퇴한 회원의 토큰으로 내 정보 조회 (토큰은 아직 30분간 유효)
    // 기대: 401 TOKEN_INVALID
    @Test
    @DisplayName("탈퇴한 회원의 토큰은 유효기간이 남아도 거부된다")
    void get_me_after_withdrawal() throws Exception {
        userRepository.delete(userRepository.findById(userId).orElseThrow()); // soft delete

        mockMvc.perform(get("/api/users/me")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }
}
