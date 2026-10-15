package com.cosmos.api.event.service;

import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.repository.EventQueryRepository;
import com.cosmos.api.event.repository.EventQueryRepository.EventRow;
import com.cosmos.api.event.repository.EventSearchCondition;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class EventService {

    static final Set<String> VALUE_CHAINS = Set.of("SEMICONDUCTOR", "BATTERY", "AUTO");

    private final EventQueryRepository eventRepository;
    private final TimeIdCursorCodec cursorCodec;

    public EventService(EventQueryRepository eventRepository, TimeIdCursorCodec cursorCodec) {
        this.eventRepository = eventRepository;
        this.cursorCodec = cursorCodec;
    }

    public CursorPageResponse<EventSummaryResponse> findEvents(
            String valueChain,
            UUID companyId,
            boolean hasInferred,
            String cursor,
            int size
    ) {
        if (size < 1 || size > 100) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        if (valueChain != null && !VALUE_CHAINS.contains(valueChain)) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        TimeIdCursor decoded = cursorCodec.decode(cursor);
        List<EventRow> rows = eventRepository.findEvents(
                new EventSearchCondition(valueChain, companyId, hasInferred), decoded, size + 1);

        boolean hasNext = rows.size() > size;
        List<EventRow> pageRows = hasNext ? rows.subList(0, size) : rows;
        Map<UUID, List<EventCompanyRefResponse>> reported =
                eventRepository.findReportedCompanies(pageRows.stream().map(EventRow::eventId).toList());
        List<EventSummaryResponse> items = pageRows.stream()
                .map(row -> summary(row, reported.getOrDefault(row.eventId(), List.of())))
                .toList();

        String nextCursor = null;
        if (hasNext && !pageRows.isEmpty()) {
            EventRow last = pageRows.getLast();
            nextCursor = cursorCodec.encode(new TimeIdCursor(last.dateline(), last.eventId()));
        }
        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    static EventSummaryResponse summary(EventRow row, List<EventCompanyRefResponse> reportedCompanies) {
        return new EventSummaryResponse(
                row.eventId(), row.newsId(), row.title(), row.summary(), row.publisher(), row.originalUrl(),
                row.dateline(), row.tradingDate(), row.valueChain(), row.categories(), reportedCompanies,
                row.inferredCount(), row.holdCount());
    }
}
