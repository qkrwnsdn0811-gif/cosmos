package com.cosmos.api.global.security;

import com.cosmos.api.global.error.ErrorCode;
import com.cosmos.api.global.error.ErrorResponse;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

/**
 * 인증 실패 응답을 직접 JSON으로 써준다.
 * 이 단계는 컨트롤러 이전이라 GlobalExceptionHandler가 잡아주지 못하므로,
 * 같은 형식(code/message/fieldErrors)을 여기서 맞춰준다.
 */
@Component
@RequiredArgsConstructor
public class AuthErrorResponder {

    private final ObjectMapper objectMapper;

    public void write(HttpServletResponse response, ErrorCode errorCode) throws IOException {
        response.setStatus(errorCode.status().value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.getWriter().write(objectMapper.writeValueAsString(ErrorResponse.from(errorCode)));
    }
}
