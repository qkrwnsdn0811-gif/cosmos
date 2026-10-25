package com.cosmos.api.company.dto;

import java.time.LocalDate;
import java.util.List;

/** asOf 는 종목들의 마지막 거래일(주가가 하나도 없으면 null). items 는 시가총액 내림차순, 시가총액이 없으면 이름순으로 뒤에. */
public record CompanyQuoteListResponse(LocalDate asOf, List<CompanyQuoteResponse> items) {
}
