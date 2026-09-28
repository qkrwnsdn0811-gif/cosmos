package com.cosmos.api.news.service;

import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyRepository;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.news.dto.NewsCompanySummaryResponse;
import com.cosmos.api.news.dto.NewsDetailResponse;
import com.cosmos.api.news.dto.NewsEvidenceResponse;
import com.cosmos.api.news.dto.NewsRelatedCompanyResponse;
import com.cosmos.api.news.dto.NewsSummaryResponse;
import com.cosmos.api.news.error.NewsErrorCode;
import com.cosmos.api.news.repository.NewsQueryRepository;
import com.cosmos.api.news.repository.NewsQueryRepository.EvidenceRow;
import com.cosmos.api.news.repository.NewsQueryRepository.NewsDetailRow;
import com.cosmos.api.news.repository.NewsQueryRepository.NewsRow;
import com.cosmos.api.news.repository.NewsQueryRepository.RelatedCompanyRow;
import com.cosmos.api.news.repository.NewsSearchCondition;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class NewsService {

    private final NewsQueryRepository newsRepository;
    private final CompanyRepository companyRepository;
    private final NewsCursorCodec cursorCodec;

    public NewsService(
            NewsQueryRepository newsRepository,
            CompanyRepository companyRepository,
            NewsCursorCodec cursorCodec
    ) {
        this.newsRepository = newsRepository;
        this.companyRepository = companyRepository;
        this.cursorCodec = cursorCodec;
    }

    public CursorPageResponse<NewsSummaryResponse> findNews(
            NewsSearchCondition condition,
            Long userId
    ) {
        if (condition.size() < 1 || condition.size() > 100) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        if (condition.companyId() != null
                && companyRepository.findActiveById(condition.companyId()).isEmpty()) {
            throw new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND);
        }
        if (condition.from() != null && condition.to() != null
                && condition.from().isAfter(condition.to())) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }

        NewsCursor cursor = cursorCodec.decode(condition.cursor());
        Instant fromInclusive = condition.from() == null
                ? null : condition.from().atStartOfDay(ZoneOffset.UTC).toInstant();
        Instant toExclusive = condition.to() == null
                ? null : condition.to().plusDays(1).atStartOfDay(ZoneOffset.UTC).toInstant();
        List<NewsRow> rows = newsRepository.search(
                condition,
                fromInclusive,
                toExclusive,
                cursor == null ? null : cursor.publishedAt(),
                cursor == null ? null : cursor.newsId(),
                userId);

        boolean hasNext = rows.size() > condition.size();
        List<NewsRow> pageRows = hasNext ? rows.subList(0, condition.size()) : rows;
        Map<UUID, List<RelatedCompanyRow>> companiesByNews = relatedCompaniesByNews(pageRows);
        List<NewsSummaryResponse> items = pageRows.stream()
                .map(row -> new NewsSummaryResponse(
                        row.newsId(), row.title(), row.summary(), row.publisher(), row.originalUrl(),
                        row.publishedAt(), row.sentiment(),
                        companiesByNews.getOrDefault(row.newsId(), List.of()).stream()
                                .map(company -> new NewsCompanySummaryResponse(
                                        company.companyId(), company.name()))
                                .toList(),
                        row.scrapped()))
                .toList();

        String nextCursor = null;
        if (hasNext && !pageRows.isEmpty()) {
            NewsRow last = pageRows.getLast();
            nextCursor = cursorCodec.encode(new NewsCursor(last.publishedAt(), last.newsId()));
        }
        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    public NewsDetailResponse findNewsDetail(UUID newsId, Long userId) {
        NewsDetailRow news = newsRepository.findDetail(newsId, userId)
                .orElseThrow(() -> new BusinessException(NewsErrorCode.NEWS_NOT_FOUND));
        List<NewsRelatedCompanyResponse> companies = newsRepository
                .findRelatedCompanies(List.of(newsId)).stream()
                .map(this::toRelatedCompany)
                .toList();
        List<NewsEvidenceResponse> evidence = newsRepository.findEvidence(newsId).stream()
                .map(this::toEvidence)
                .toList();
        return new NewsDetailResponse(
                news.newsId(), news.title(), news.summary(), news.publisher(), news.author(),
                news.originalUrl(), news.publishedAt(), companies, evidence, news.scrapped());
    }

    private Map<UUID, List<RelatedCompanyRow>> relatedCompaniesByNews(List<NewsRow> rows) {
        List<UUID> newsIds = rows.stream().map(NewsRow::newsId).toList();
        Map<UUID, List<RelatedCompanyRow>> result = new HashMap<>();
        for (RelatedCompanyRow company : newsRepository.findRelatedCompanies(newsIds)) {
            result.computeIfAbsent(company.newsId(), ignored -> new ArrayList<>()).add(company);
        }
        return result;
    }

    private NewsRelatedCompanyResponse toRelatedCompany(RelatedCompanyRow row) {
        return new NewsRelatedCompanyResponse(
                row.companyId(), row.name(), row.sentiment(),
                row.relevanceScore(), row.impactScore());
    }

    private NewsEvidenceResponse toEvidence(EvidenceRow row) {
        return new NewsEvidenceResponse(row.sentence(), row.confidence());
    }
}
