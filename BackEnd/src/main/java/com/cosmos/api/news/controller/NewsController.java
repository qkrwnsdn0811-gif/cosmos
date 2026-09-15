package com.cosmos.api.news.controller;

import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthUser;
import com.cosmos.api.news.dto.NewsDetailResponse;
import com.cosmos.api.news.dto.NewsSummaryResponse;
import com.cosmos.api.news.repository.NewsSearchCondition;
import com.cosmos.api.news.service.NewsService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import java.time.LocalDate;
import java.util.UUID;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@Validated
@RestController
@RequestMapping("/api/news")
public class NewsController {

    private final NewsService newsService;

    public NewsController(NewsService newsService) {
        this.newsService = newsService;
    }

    @GetMapping
    public CursorPageResponse<NewsSummaryResponse> findNews(
            @RequestParam(required = false) @Size(max = 200) String keyword,
            @RequestParam(required = false) UUID companyId,
            @RequestParam(required = false) UUID industryId,
            @RequestParam(required = false) String sentiment,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate to,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size,
            @AuthenticationPrincipal AuthUser authUser
    ) {
        return newsService.findNews(
                new NewsSearchCondition(
                        keyword, companyId, industryId, sentiment, from, to, cursor, size),
                userId(authUser));
    }

    @GetMapping("/{newsId}")
    public NewsDetailResponse findNewsDetail(
            @PathVariable UUID newsId,
            @AuthenticationPrincipal AuthUser authUser
    ) {
        return newsService.findNewsDetail(newsId, userId(authUser));
    }

    private Long userId(AuthUser authUser) {
        return authUser == null ? null : authUser.userId();
    }
}
