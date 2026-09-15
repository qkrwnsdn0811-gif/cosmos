package com.cosmos.api.global.response;

import java.util.List;

public record CursorPageResponse<T>(
        List<T> items,
        String nextCursor,
        boolean hasNext
) {

    public CursorPageResponse {
        items = List.copyOf(items);
    }

    public static <T> CursorPageResponse<T> of(List<T> items, String nextCursor, boolean hasNext) {
        return new CursorPageResponse<>(items, nextCursor, hasNext);
    }
}
