package com.cosmos.api.user.service;

import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.stereotype.Component;

/** 인증 코드 메일 한 통을 만들어 보낸다. 내용은 텍스트 한 장으로 단순하게 유지한다. */
@Component
public class VerificationMailSender {

    private final JavaMailSender mailSender;
    private final String from;

    public VerificationMailSender(JavaMailSender mailSender, @Value("${spring.mail.username:}") String from) {
        this.mailSender = mailSender;
        this.from = from;
    }

    public void sendCode(String to, String code, Duration validity) {
        SimpleMailMessage message = new SimpleMailMessage();
        if (!from.isBlank()) {
            message.setFrom(from);
        }
        message.setTo(to);
        message.setSubject("[COSMOS] 이메일 인증 코드");
        message.setText("""
                COSMOS 회원가입 인증 코드입니다.

                인증 코드: %s

                이 코드는 %d분 동안만 유효합니다.
                본인이 요청하지 않았다면 이 메일을 무시하셔도 됩니다.
                """.formatted(code, validity.toMinutes()));
        mailSender.send(message);
    }
}
