package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.repository.UserRepository;
import com.cosmos.api.user.service.EmailVerificationStore;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 회원가입 ↔ 이메일 인증 연동 테스트.
 * "인증 안 하면 못 가입", "인증하면 가입되고 인증 시각이 남음", "인증 한 번으로 두 번 가입 불가"를 확인한다.
 * 인증 완료 표시는 코드 확인 API를 거친 것처럼 Redis에 직접 넣는다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // DB 변경은 자동으로 되돌아간다. Redis는 그렇지 않아서 아래 @AfterEach로 직접 지운다
class SignupEmailVerificationTest {

    private static final String EMAIL = "signup-verify@test.com";

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    UserRepository userRepository;

    @Autowired
    EmailVerificationStore store;

    @AfterEach
    void cleanRedis() {
        store.clear(EMAIL);
    }

    private ResultActions signup(String email, String nickname) throws Exception {
        return mockMvc.perform(post("/api/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new SignupRequest(email, "password1", nickname))));
    }

    // 상황: 이메일 인증을 거치지 않고 바로 가입 요청
    // 기대: 403 EMAIL_NOT_VERIFIED, 회원은 만들어지지 않는다
    @Test
    @DisplayName("이메일 인증 없이 가입하면 403 EMAIL_NOT_VERIFIED")
    void signup_without_verification() throws Exception {
        signup(EMAIL, "미인증가입")
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("EMAIL_NOT_VERIFIED"));

        assertThat(userRepository.existsByEmail(EMAIL)).isFalse();
    }

    // 상황: 인증 완료 표시가 있는 상태에서 가입
    // 기대: 201, 회원의 email_verified_at 이 채워지고, 인증 완료 표시는 사라진다
    @Test
    @DisplayName("이메일 인증을 마쳤으면 가입되고 인증 시각이 기록된다")
    void signup_after_verification() throws Exception {
        store.markVerified(EMAIL);

        signup(EMAIL, "인증가입")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.email").value(EMAIL));

        User user = userRepository.findByEmail(EMAIL).orElseThrow();
        assertThat(user.getEmailVerifiedAt()).isNotNull();
        assertThat(store.isVerified(EMAIL)).isFalse();
    }

    // 상황: 대문자가 섞인 이메일로 가입 (인증은 소문자 주소로 완료된 상태)
    // 기대: 소문자로 맞춰 같은 인증으로 인정되어 가입된다
    @Test
    @DisplayName("이메일 대소문자가 달라도 같은 인증으로 가입된다")
    void signup_matches_verification_case_insensitively() throws Exception {
        store.markVerified(EMAIL);

        signup("Signup-Verify@Test.com", "대문자가입")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.email").value(EMAIL));
    }

    // 상황: 인증을 한 번만 하고, 같은 이메일로 가입을 두 번 시도
    // 기대: 첫 번째 201, 두 번째는 이미 가입된 이메일이라 409 (인증 표시가 지워졌어도 중복 검사가 먼저)
    @Test
    @DisplayName("가입이 끝난 이메일은 다시 가입할 수 없다")
    void signup_twice_same_email() throws Exception {
        store.markVerified(EMAIL);
        signup(EMAIL, "첫번째").andExpect(status().isCreated());

        signup(EMAIL, "두번째")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("EMAIL_DUPLICATED"));
    }

    // 상황: 인증은 했지만 닉네임이 중복되어 가입 실패
    // 기대: 409 NICKNAME_DUPLICATED 이고, 인증 완료 표시는 남아 있어 닉네임만 바꿔 다시 가입할 수 있다
    @Test
    @DisplayName("닉네임 중복으로 실패해도 인증 상태는 유지된다")
    void signup_nickname_conflict_keeps_verification() throws Exception {
        store.markVerified("other-" + EMAIL);
        signup("other-" + EMAIL, "겹치는닉네임").andExpect(status().isCreated());

        store.markVerified(EMAIL);
        signup(EMAIL, "겹치는닉네임")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NICKNAME_DUPLICATED"));
        assertThat(store.isVerified(EMAIL)).isTrue();

        signup(EMAIL, "다른닉네임").andExpect(status().isCreated());
        store.clear("other-" + EMAIL);
    }
}
