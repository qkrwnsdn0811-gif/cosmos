package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.repository.UserRepository;
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
 * 회원가입 API 테스트.
 * 가입 성공(201), 비밀번호 암호화 저장, 이메일·닉네임 중복(409),
 * 입력 규칙 위반(400)이 명세대로 동작하는지 검증한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // 각 테스트가 끝나면 DB 변경을 되돌려서 테스트끼리 영향을 주지 않게 한다
class AuthControllerTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    UserRepository userRepository;

    // 테스트 공통: 회원가입 요청을 보내고 결과를 돌려준다
    private ResultActions signup(String email, String password, String nickname) throws Exception {
        SignupRequest request = new SignupRequest(email, password, nickname);
        return mockMvc.perform(post("/api/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(request)));
    }

    // 상황: 올바른 값으로 가입 요청 (이메일에 대문자 포함)
    // 기대: 201 + 회원 정보 반환, 이메일은 소문자로 바뀌어 저장
    @Test
    @DisplayName("회원가입 성공 시 201과 회원 정보를 반환하고, 이메일은 소문자로 정규화된다")
    void signup_success() throws Exception {
        signup("Kim@Test.com", "password1", "우주탐험가")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.userId").isNumber())
                .andExpect(jsonPath("$.email").value("kim@test.com"))
                .andExpect(jsonPath("$.nickname").value("우주탐험가"));
    }

    // 상황: 가입 후 DB에 저장된 비밀번호를 직접 확인
    // 기대: 입력한 평문("password1")이 아니라 BCrypt 해시($2로 시작)가 저장됨
    @Test
    @DisplayName("비밀번호는 평문이 아닌 BCrypt 해시로 저장된다")
    void password_is_hashed() throws Exception {
        signup("hash@test.com", "password1", "해시검증").andExpect(status().isCreated());

        User saved = userRepository.findByEmail("hash@test.com").orElseThrow();
        assertThat(saved.getPassword()).isNotEqualTo("password1");
        assertThat(saved.getPassword()).startsWith("$2"); // BCrypt 해시의 고정 앞글자
    }

    // 상황: dup@test.com으로 가입된 상태에서 DUP@test.com으로 다시 가입 시도
    // 기대: 대소문자만 달라도 같은 이메일 → 409 + EMAIL_DUPLICATED
    @Test
    @DisplayName("이미 가입된 이메일이면 대소문자가 달라도 409 EMAIL_DUPLICATED를 반환한다")
    void signup_duplicate_email() throws Exception {
        signup("dup@test.com", "password1", "첫번째").andExpect(status().isCreated());

        signup("DUP@test.com", "password1", "두번째")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("EMAIL_DUPLICATED"));
    }

    // 상황: 같은 닉네임으로 두 번째 가입 시도 (이메일은 다름)
    // 기대: 409 + NICKNAME_DUPLICATED
    @Test
    @DisplayName("이미 사용 중인 닉네임이면 409 NICKNAME_DUPLICATED를 반환한다")
    void signup_duplicate_nickname() throws Exception {
        signup("nick1@test.com", "password1", "같은닉네임").andExpect(status().isCreated());

        signup("nick2@test.com", "password1", "같은닉네임")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NICKNAME_DUPLICATED"));
    }

    // 상황: 숫자가 하나도 없는 비밀번호로 가입 시도
    // 기대: 400 + VALIDATION_FAILED, fieldErrors에 password가 지목됨
    @Test
    @DisplayName("비밀번호에 숫자가 없으면 400과 password 필드 에러를 반환한다")
    void signup_invalid_password() throws Exception {
        signup("weak@test.com", "passwordonly", "약한비번")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("password"));
    }
}
