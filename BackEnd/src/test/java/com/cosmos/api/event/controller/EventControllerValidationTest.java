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

    @Test
    void listRejectsUnknownNewsType() throws Exception {
        mockMvc.perform(get("/api/events").param("valueChain", "SEMICONDUCTOR").param("newsType", "FOO"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    /** 고정 경로가 /{eventId}(UUID)로 잡혀 400이 되지 않고, 토큰 없이 200이 되는지 본다. */
    @Test
    void investorRoutesArePublicAndNotCapturedByEventId() throws Exception {
        mockMvc.perform(get("/api/events/backtest").param("valueChain", "SEMICONDUCTOR"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.valueChain").value("SEMICONDUCTOR"))
                .andExpect(jsonPath("$.rows").isArray());
        mockMvc.perform(get("/api/events/company-history")
                        .param("companyId", "7e000000-0000-0000-0000-000000000098").param("size", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isArray());
    }

    @Test
    void investorRoutesValidateParams() throws Exception {
        mockMvc.perform(get("/api/events/backtest"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        mockMvc.perform(get("/api/events/company-history"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
        mockMvc.perform(get("/api/events/company-history")
                        .param("companyId", "7e000000-0000-0000-0000-000000000098").param("size", "101"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    @Test
    void chartAndFundamentalArePublic404ForUnknownIds() throws Exception {
        mockMvc.perform(get("/api/events/{eventId}/companies/{companyId}/chart",
                        "7e000000-0000-0000-0000-000000000097", "7e000000-0000-0000-0000-000000000098"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("EVENT_NOT_FOUND"));
        mockMvc.perform(get("/api/companies/{companyId}/fundamental", "7e000000-0000-0000-0000-000000000098"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }
}
