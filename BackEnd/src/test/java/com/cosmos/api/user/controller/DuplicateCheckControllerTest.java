package com.cosmos.api.user.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.dto.SignupRequest;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 이메일·닉네임 중복 확인 API 테스트.
 * 회원가입 폼에서 실시간으로 "이거 쓸 수 있나요?"를 물어보는 API가
 * 명세(200 + available, 형식 오류 시 400)대로 동작하는지 검증한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // 각 테스트가 끝나면 DB 변경을 되돌려서 테스트끼리 영향을 주지 않게 한다
class DuplicateCheckControllerTest {

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    // 테스트 준비용: 회원 한 명을 미리 가입시켜 둔다
    private void signup(String email, String nickname) throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new SignupRequest(email, "password1", nickname))))
                .andExpect(status().isCreated());
    }

    // 상황: 아무도 안 쓰는 이메일로 확인 요청
    // 기대: 200 OK + available = true
    @Test
    @DisplayName("아무도 안 쓰는 이메일이면 available true")
    void check_email_available() throws Exception {
        mockMvc.perform(get("/api/auth/check-email").param("email", "free@test.com"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(true));
    }

    // 상황: taken@test.com으로 가입된 상태에서 TAKEN@test.com으로 확인 요청
    // 기대: 대소문자만 다른 이메일은 같은 것으로 취급 → available = false
    @Test
    @DisplayName("이미 가입된 이메일이면 대소문자가 달라도 available false")
    void check_email_taken_case_insensitive() throws Exception {
        signup("taken@test.com", "선점닉네임");

        mockMvc.perform(get("/api/auth/check-email").param("email", "TAKEN@test.com"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(false));
    }

    // 상황: 이메일 모양이 아닌 값(not-an-email)으로 확인 요청
    // 기대: 400 + code = VALIDATION_FAILED
    @Test
    @DisplayName("이메일 형식이 틀리면 400 VALIDATION_FAILED")
    void check_email_invalid_format() throws Exception {
        mockMvc.perform(get("/api/auth/check-email").param("email", "not-an-email"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 아무도 안 쓰는 닉네임으로 확인 요청
    // 기대: 200 OK + available = true
    @Test
    @DisplayName("아무도 안 쓰는 닉네임이면 available true")
    void check_nickname_available() throws Exception {
        mockMvc.perform(get("/api/auth/check-nickname").param("nickname", "새닉네임"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(true));
    }

    // 상황: 이미 누가 쓰고 있는 닉네임으로 확인 요청
    // 기대: 200 OK + available = false
    @Test
    @DisplayName("이미 쓰는 닉네임이면 available false")
    void check_nickname_taken() throws Exception {
        signup("nick@test.com", "쓰는중닉네임");

        mockMvc.perform(get("/api/auth/check-nickname").param("nickname", "쓰는중닉네임"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.available").value(false));
    }

    // 상황: 닉네임 최소 길이(2자)보다 짧은 1자로 확인 요청
    // 기대: 400 + code = VALIDATION_FAILED
    @Test
    @DisplayName("닉네임이 1자면 400 VALIDATION_FAILED")
    void check_nickname_too_short() throws Exception {
        mockMvc.perform(get("/api/auth/check-nickname").param("nickname", "a"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }
}
