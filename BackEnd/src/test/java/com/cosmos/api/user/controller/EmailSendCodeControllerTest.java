package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.user.dto.EmailSendCodeRequest;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.service.EmailVerificationStore;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.mail.MailSendException;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 인증 코드 발송 API 테스트.
 * 실제 메일은 보내지 않도록 메일 발송기를 가짜(mock)로 바꿔 끼우고, "메일이 나갔는지"와 "Redis에 코드가 남았는지"를 본다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional // DB 변경은 자동으로 되돌아간다. Redis는 그렇지 않아서 아래 @AfterEach로 직접 지운다
class EmailSendCodeControllerTest {

    private static final String EMAIL = "verify@test.com";

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    EmailVerificationStore store;

    @MockitoBean
    JavaMailSender mailSender;

    @AfterEach
    void cleanRedis() {
        store.clear(EMAIL);
        store.clear("kim@test.com");
    }

    private ResultActions sendCode(String email) throws Exception {
        return mockMvc.perform(post("/api/auth/email/send-code")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new EmailSendCodeRequest(email))));
    }

    // 상황: 가입 안 된 이메일로 코드 발송 요청 (토큰 없이)
    // 기대: 200 + 유효 시간 300초, 그 주소로 메일 한 통, Redis에 6자리 숫자 코드가 남는다
    @Test
    @DisplayName("코드를 보내면 메일이 나가고 Redis에 6자리 코드가 저장된다")
    void send_code_success() throws Exception {
        sendCode(EMAIL)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value(EMAIL))
                .andExpect(jsonPath("$.expiresInSeconds").value(300));

        ArgumentCaptor<SimpleMailMessage> captor = ArgumentCaptor.forClass(SimpleMailMessage.class);
        verify(mailSender).send(captor.capture());
        SimpleMailMessage mail = captor.getValue();
        assertThat(mail.getTo()).containsExactly(EMAIL);

        String code = store.findCode(EMAIL).orElseThrow();
        assertThat(code).matches("\\d{6}");
        assertThat(mail.getText()).contains(code); // 메일 본문에 실제 저장된 코드가 들어 있다
    }

    // 상황: 대문자가 섞인 이메일로 요청
    // 기대: 소문자로 정규화해서 처리·저장한다 (가입 시 소문자 저장 규칙과 같음)
    @Test
    @DisplayName("이메일은 소문자로 정규화해서 처리한다")
    void send_code_normalizes_email() throws Exception {
        sendCode("Kim@Test.com")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value("kim@test.com"));

        assertThat(store.findCode("kim@test.com")).isPresent();
    }

    // 상황: 이미 가입된 이메일로 코드 발송 요청
    // 기대: 409 EMAIL_DUPLICATED, 메일은 나가지 않는다
    @Test
    @DisplayName("이미 가입된 이메일이면 409이고 메일을 보내지 않는다")
    void send_code_duplicated_email() throws Exception {
        store.markVerified(EMAIL); // 인증 단계를 거친 것으로 처리
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new SignupRequest(EMAIL, "password1", "인증테스터"))))
                .andExpect(status().isCreated());

        sendCode(EMAIL)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("EMAIL_DUPLICATED"));

        verify(mailSender, never()).send(any(SimpleMailMessage.class));
    }

    // 상황: 코드를 보낸 직후 같은 이메일로 다시 요청
    // 기대: 429 EMAIL_CODE_TOO_FREQUENT, 메일은 처음 한 통만
    @Test
    @DisplayName("60초 안에 다시 요청하면 429")
    void send_code_too_frequent() throws Exception {
        sendCode(EMAIL).andExpect(status().isOk());

        sendCode(EMAIL)
                .andExpect(status().isTooManyRequests())
                .andExpect(jsonPath("$.code").value("EMAIL_CODE_TOO_FREQUENT"));

        verify(mailSender).send(any(SimpleMailMessage.class)); // 정확히 1회
    }

    // 상황: 이메일 형식이 아닌 값으로 요청
    // 기대: 400 VALIDATION_FAILED + email 필드 오류
    @Test
    @DisplayName("이메일 형식이 잘못되면 400")
    void send_code_invalid_email() throws Exception {
        sendCode("not-an-email")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("email"));

        verify(mailSender, never()).send(any(SimpleMailMessage.class));
    }

    // 상황: 메일 서버 장애로 발송이 실패
    // 기대: 500, 받지 못한 코드가 Redis에 남지 않는다 (다음 요청이 바로 가능해야 하므로 재발송 제한도 없음)
    @Test
    @DisplayName("메일 발송이 실패하면 코드를 저장하지 않는다")
    void send_code_mail_failure_saves_nothing() throws Exception {
        doThrow(new MailSendException("SMTP down")).when(mailSender).send(any(SimpleMailMessage.class));

        sendCode(EMAIL)
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("INTERNAL_SERVER_ERROR"));

        assertThat(store.findCode(EMAIL)).isEmpty();
        assertThat(store.isInCooldown(EMAIL)).isFalse();
    }
}
