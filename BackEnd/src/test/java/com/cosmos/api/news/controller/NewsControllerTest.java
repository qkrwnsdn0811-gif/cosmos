package com.cosmos.api.news.controller;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.error.GlobalExceptionHandler;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.news.dto.NewsCompanySummaryResponse;
import com.cosmos.api.news.dto.NewsDetailResponse;
import com.cosmos.api.news.dto.NewsEvidenceResponse;
import com.cosmos.api.news.dto.NewsRelatedCompanyResponse;
import com.cosmos.api.news.dto.NewsSummaryResponse;
import com.cosmos.api.news.repository.NewsSearchCondition;
import com.cosmos.api.news.service.NewsService;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class NewsControllerTest {

    private static final UUID NEWS_ID = UUID.fromString("71000000-0000-0000-0000-000000000001");
    private static final UUID COMPANY_ID = UUID.fromString("71000000-0000-0000-0000-000000000002");
    private static final Instant PUBLISHED_AT = Instant.parse("2026-09-07T02:00:00Z");

    private NewsService newsService;
    private MockMvc mockMvc;

    @BeforeEach
    void setUp() {
        newsService = mock(NewsService.class);
        mockMvc = MockMvcBuilders.standaloneSetup(new NewsController(newsService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void returnsNewsCursorPageWithoutSuccessWrapper() throws Exception {
        when(newsService.findNews(any(), isNull())).thenReturn(CursorPageResponse.of(
                List.of(new NewsSummaryResponse(
                        NEWS_ID, "뉴스 제목", "기사 요약", "예시경제",
                        "https://example.com/news", PUBLISHED_AT, "POSITIVE",
                        List.of(new NewsCompanySummaryResponse(COMPANY_ID, "기업A")), false)),
                "next-token", true));

        mockMvc.perform(get("/api/news")
                        .param("keyword", "반도체")
                        .param("companyId", COMPANY_ID.toString())
                        .param("sentiment", "positive")
                        .param("from", "2026-09-01")
                        .param("to", "2026-09-07")
                        .param("size", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_ID.toString()))
                .andExpect(jsonPath("$.items[0].relatedCompanies[0].companyId")
                        .value(COMPANY_ID.toString()))
                .andExpect(jsonPath("$.items[0].scrapped").value(false))
                .andExpect(jsonPath("$.nextCursor").value("next-token"))
                .andExpect(jsonPath("$.hasNext").value(true));

        ArgumentCaptor<NewsSearchCondition> condition = ArgumentCaptor.forClass(NewsSearchCondition.class);
        verify(newsService).findNews(condition.capture(), isNull());
        org.assertj.core.api.Assertions.assertThat(condition.getValue().sentiment()).isEqualTo("POSITIVE");
        org.assertj.core.api.Assertions.assertThat(condition.getValue().size()).isEqualTo(10);
    }

    @Test
    void returnsNewsDetail() throws Exception {
        when(newsService.findNewsDetail(NEWS_ID, null)).thenReturn(new NewsDetailResponse(
                NEWS_ID, "뉴스 제목", "기사 요약", "예시경제", "홍길동",
                "https://example.com/news", PUBLISHED_AT,
                List.of(new NewsRelatedCompanyResponse(
                        COMPANY_ID, "기업A", "POSITIVE",
                        new BigDecimal("0.94"), new BigDecimal("0.81"))),
                List.of(new NewsEvidenceResponse("관계 근거 문장", new BigDecimal("0.91"))),
                false));

        mockMvc.perform(get("/api/news/{newsId}", NEWS_ID))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.newsId").value(NEWS_ID.toString()))
                .andExpect(jsonPath("$.relatedCompanies[0].sentiment").value("POSITIVE"))
                .andExpect(jsonPath("$.evidence[0].sentence").value("관계 근거 문장"));
    }
}
