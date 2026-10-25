package com.cosmos.api.company.dto;

import java.util.List;
import java.util.UUID;

/** 공개 은하 스냅샷의 window(1Y) 관계 기업, 점수 내림차순. */
public record RelatedCompanyListResponse(UUID companyId, String window, List<RelatedCompanyResponse> items) {
}
