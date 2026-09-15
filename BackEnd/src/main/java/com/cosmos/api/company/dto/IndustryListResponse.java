package com.cosmos.api.company.dto;

import java.util.List;

public record IndustryListResponse(
        List<IndustryResponse> items
) {

    public IndustryListResponse {
        items = List.copyOf(items);
    }
}
