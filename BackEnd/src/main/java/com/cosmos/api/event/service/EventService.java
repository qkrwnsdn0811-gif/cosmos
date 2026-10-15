package com.cosmos.api.event.service;

import com.cosmos.api.event.dto.AbnormalReturnResponse;
import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventNodeResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.dto.PathStepResponse;
import com.cosmos.api.event.error.EventErrorCode;
import com.cosmos.api.event.repository.EventQueryRepository;
import com.cosmos.api.event.repository.EventQueryRepository.AbnormalReturnRow;
import com.cosmos.api.event.repository.EventQueryRepository.CompanyKeyRow;
import com.cosmos.api.event.repository.EventQueryRepository.EventRow;
import com.cosmos.api.event.repository.EventQueryRepository.ImpactRow;
import com.cosmos.api.event.service.EventPathParser.RawStep;
import com.cosmos.api.event.repository.EventSearchCondition;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional(readOnly = true)
public class EventService {

    static final Set<String> VALUE_CHAINS = Set.of("SEMICONDUCTOR", "BATTERY", "AUTO");

    static final int SNIPPETS_PER_STEP = 5;

    private final EventQueryRepository eventRepository;
    private final TimeIdCursorCodec cursorCodec;
    private final EventPathParser pathParser;

    public EventService(EventQueryRepository eventRepository, TimeIdCursorCodec cursorCodec,
            EventPathParser pathParser) {
        this.eventRepository = eventRepository;
        this.cursorCodec = cursorCodec;
        this.pathParser = pathParser;
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
        // 밸류체인은 필수다: 없으면 (value_chain, dateline) 인덱스를 못 타고 이벤트 전체를 훑는다
        if (valueChain == null || !VALUE_CHAINS.contains(valueChain)) {
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

    public EventMapResponse findEventMap(UUID eventId) {
        EventRow event = eventRepository.findEvent(eventId)
                .orElseThrow(() -> new BusinessException(EventErrorCode.EVENT_NOT_FOUND));
        List<ImpactRow> impacts = eventRepository.findImpacts(eventId);
        Map<UUID, List<AbnormalReturnResponse>> returns = eventRepository.findAbnormalReturns(eventId).stream()
                .collect(Collectors.groupingBy(AbnormalReturnRow::companyId, LinkedHashMap::new,
                        Collectors.mapping(r -> new AbnormalReturnResponse(r.windowDays(), r.benchmark(),
                                r.stockReturn(), r.benchmarkReturn(), r.abnormalReturn()), Collectors.toList())));

        Map<UUID, List<RawStep>> paths = new LinkedHashMap<>();
        impacts.forEach(i -> paths.put(i.companyId(), pathParser.parse(i.pathJson())));
        Map<String, CompanyKeyRow> companies = eventRepository.findCompaniesByKeys(paths.values().stream()
                .flatMap(List::stream).flatMap(s -> Stream.of(s.src(), s.dst())).filter(Objects::nonNull)
                .collect(Collectors.toSet()));
        Set<UUID> reported = new HashSet<>();
        impacts.stream().filter(i -> "REPORTED".equals(i.basis())).forEach(i -> reported.add(i.companyId()));

        List<EventNodeResponse> nodes = impacts.stream().map(i -> {
            List<PathStepResponse> steps = paths.get(i.companyId()).stream().map(s -> step(s, companies)).toList();
            UUID anchor = "INFERRED".equals(i.basis()) && !steps.isEmpty() ? anchorOf(steps.getFirst(), reported) : null;
            return new EventNodeResponse(i.companyId(), i.name(), i.stockCode(), i.market(), i.basis(), i.hop(),
                    i.score(), i.status(), i.holdReason(), i.impactDirection(), i.impactMagnitude(), i.confidence(), anchor,
                    steps,
                    returns.getOrDefault(i.companyId(), List.of()));
        }).toList();
        List<EventCompanyRefResponse> reportedCompanies = impacts.stream()
                .filter(i -> "REPORTED".equals(i.basis()))
                .map(i -> new EventCompanyRefResponse(i.companyId(), i.name(), i.stockCode(), i.market()))
                .toList();
        return new EventMapResponse(summary(event, reportedCompanies), nodes);
    }

    private PathStepResponse step(RawStep s, Map<String, CompanyKeyRow> companies) {
        CompanyKeyRow source = s.src() == null ? null : companies.get(s.src());
        CompanyKeyRow target = s.dst() == null ? null : companies.get(s.dst());
        boolean withEvidence = "NEWS".equals(s.source()) && source != null && target != null && s.rel() != null;
        return new PathStepResponse(
                source == null ? null : source.companyId(), source == null ? null : source.name(),
                target == null ? null : target.companyId(), target == null ? null : target.name(),
                s.rel(), s.source(), s.strength(),
                withEvidence ? eventRepository.findSnippets(source.companyId(), target.companyId(), s.rel(),
                        SNIPPETS_PER_STEP) : List.of());
    }

    /** 첫 단계의 두 끝 중 이 이벤트의 언급 기업. 전파는 언급 기업에서 시작하므로 둘 중 하나다. */
    private static UUID anchorOf(PathStepResponse first, Set<UUID> reported) {
        if (first.sourceCompanyId() != null && reported.contains(first.sourceCompanyId())) {
            return first.sourceCompanyId();
        }
        if (first.targetCompanyId() != null && reported.contains(first.targetCompanyId())) {
            return first.targetCompanyId();
        }
        return null;
    }

    static EventSummaryResponse summary(EventRow row, List<EventCompanyRefResponse> reportedCompanies) {
        return new EventSummaryResponse(
                row.eventId(), row.newsId(), row.title(), row.summary(), row.publisher(), row.originalUrl(),
                row.dateline(), row.tradingDate(), row.valueChain(), row.categories(), reportedCompanies,
                row.inferredCount(), row.holdCount());
    }
}
