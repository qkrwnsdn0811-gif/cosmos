package com.cosmos.api.event.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 컨트롤러의 @Min/@Max는 클래스 @Validated(AOP)로 검사되어 standalone MockMvc에서는 동작하지 않는다.
 * 실제 컨텍스트로 띄워 범위 밖 size가 400이 되는지, 토큰 없이도 호출되는지(공개 경로) 함께 본다.
 */
@SpringBootTest
@AutoConfigureMockMvc
class EventControllerValidationTest {

    @Autowired
    MockMvc mockMvc;

    @Test
    void listRejectsOutOfRangeSize() throws Exception {
        mockMvc.perform(get("/api/events").param("size", "0"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        mockMvc.perform(get("/api/events").param("size", "101"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    @Test
    void listRequiresValueChain() throws Exception {
        mockMvc.perform(get("/api/events").param("size", "1"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    @Test
    void listIsPublic() throws Exception {
        mockMvc.perform(get("/api/events").param("valueChain", "SEMICONDUCTOR").param("size", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isArray());
    }
}
