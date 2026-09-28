package com.cosmos.api.news.dto;

import java.util.UUID;

public record NewsCompanySummaryResponse(UUID companyId, String name) {
}
