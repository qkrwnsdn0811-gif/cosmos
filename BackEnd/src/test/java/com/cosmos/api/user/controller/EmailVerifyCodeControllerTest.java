package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.dto.EmailVerifyCodeRequest;
import com.cosmos.api.user.service.EmailVerificationStore;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import tools.jackson.databind.ObjectMapper;

/**
 * 인증 코드 확인 API 테스트.
 * 메일을 보내는 대신 Redis에 코드를 직접 넣어 두고, "맞으면 인증 완료 표시가 남는지", "틀리면 어떻게 막는지"를 본다.
 * DB는 건드리지 않으므로 @Transactional 없이 Redis만 @AfterEach로 지운다.
 */
@SpringBootTest
@AutoConfigureMockMvc
class EmailVerifyCodeControllerTest {

    private static final String EMAIL = "verify-check@test.com";
    private static final String CODE = "482913";

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    EmailVerificationStore store;

    /** 발송 단계를 거친 것처럼 코드를 Redis에 넣어 둔다. */
    @BeforeEach
    void seedCode() {
        store.clear(EMAIL);
        store.saveCode(EMAIL, CODE);
    }

    @AfterEach
    void cleanRedis() {
        store.clear(EMAIL);
    }

    private ResultActions verify(String email, String code) throws Exception {
        return mockMvc.perform(post("/api/auth/email/verify-code")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new EmailVerifyCodeRequest(email, code))));
    }

    // 상황: 발송된 코드를 그대로 입력
    // 기대: 200 + verified=true·유효 1800초, Redis에 인증 완료 표시가 남고 코드는 폐기된다
    @Test
    @DisplayName("코드가 맞으면 인증 완료 표시가 남고 코드는 폐기된다")
    void verify_success() throws Exception {
        verify(EMAIL, CODE)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value(EMAIL))
                .andExpect(jsonPath("$.verified").value(true))
                .andExpect(jsonPath("$.validForSeconds").value(1800));

        assertThat(store.isVerified(EMAIL)).isTrue();
        assertThat(store.findCode(EMAIL)).isEmpty();
    }

    // 상황: 대문자가 섞인 이메일로 확인 요청
    // 기대: 소문자로 맞춰 같은 코드로 인정한다 (발송 때와 같은 규칙)
    @Test
    @DisplayName("이메일은 소문자로 정규화해서 확인한다")
    void verify_normalizes_email() throws Exception {
        verify("Verify-Check@Test.com", CODE)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value(EMAIL));

        assertThat(store.isVerified(EMAIL)).isTrue();
    }

    // 상황: 틀린 코드 입력
    // 기대: 400 EMAIL_CODE_INVALID, 코드는 아직 살아 있어 다시 시도할 수 있다
    @Test
    @DisplayName("코드가 틀리면 400이고 다시 시도할 수 있다")
    void verify_wrong_code() throws Exception {
        verify(EMAIL, "000000")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("EMAIL_CODE_INVALID"));

        assertThat(store.findCode(EMAIL)).contains(CODE);
        assertThat(store.isVerified(EMAIL)).isFalse();

        // 그 다음 맞는 코드를 넣으면 통과한다
        verify(EMAIL, CODE).andExpect(status().isOk());
    }

    // 상황: 5회 연속 틀림
    // 기대: 5번째까지 EMAIL_CODE_INVALID, 그 뒤엔 코드가 폐기되어 맞는 코드를 넣어도 EMAIL_CODE_EXPIRED
    @Test
    @DisplayName("5회 틀리면 코드가 폐기되어 다시 발송받아야 한다")
    void verify_too_many_attempts() throws Exception {
        for (int i = 0; i < 5; i++) {
            verify(EMAIL, "000000")
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.code").value("EMAIL_CODE_INVALID"));
        }

        assertThat(store.findCode(EMAIL)).isEmpty();
        verify(EMAIL, CODE)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("EMAIL_CODE_EXPIRED"));
    }

    // 상황: 코드를 발송받은 적 없는(또는 만료된) 이메일로 확인 요청
    // 기대: 400 EMAIL_CODE_EXPIRED
    @Test
    @DisplayName("발송된 코드가 없으면 400 EMAIL_CODE_EXPIRED")
    void verify_without_code() throws Exception {
        store.clear(EMAIL);

        verify(EMAIL, CODE)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("EMAIL_CODE_EXPIRED"));
    }

    // 상황: 인증에 성공한 뒤 같은 코드를 다시 보냄
    // 기대: 코드는 이미 폐기됐으므로 EMAIL_CODE_EXPIRED (인증 완료 표시는 그대로 유지)
    @Test
    @DisplayName("한 번 쓴 코드는 다시 쓸 수 없다")
    void verify_code_is_single_use() throws Exception {
        verify(EMAIL, CODE).andExpect(status().isOk());

        verify(EMAIL, CODE)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("EMAIL_CODE_EXPIRED"));
        assertThat(store.isVerified(EMAIL)).isTrue();
    }

    // 상황: 코드 자리에 숫자 6자리가 아닌 값
    // 기대: 400 VALIDATION_FAILED + code 필드 오류 (Redis는 건드리지 않음)
    @Test
    @DisplayName("코드 형식이 잘못되면 400 VALIDATION_FAILED")
    void verify_invalid_format() throws Exception {
        verify(EMAIL, "12ab")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("code"));

        assertThat(store.findCode(EMAIL)).contains(CODE);
    }
}
