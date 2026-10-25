package com.cosmos.api.event.service;

import com.cosmos.api.company.dto.CompanyFundamentalResponse;
import com.cosmos.api.company.repository.CompanyFundamentalQueryRepository;
import com.cosmos.api.event.dto.AbnormalReturnResponse;
import com.cosmos.api.event.dto.BacktestResponse;
import com.cosmos.api.event.dto.BacktestRowResponse;
import com.cosmos.api.event.dto.ChartPointResponse;
import com.cosmos.api.event.dto.CompanyHistoryItemResponse;
import com.cosmos.api.event.dto.CompanySignalResponse;
import com.cosmos.api.event.dto.EventChartResponse;
import com.cosmos.api.event.dto.EventCompanyRefResponse;
import com.cosmos.api.event.dto.EventMapResponse;
import com.cosmos.api.event.dto.EventNodeResponse;
import com.cosmos.api.event.dto.EventSummaryResponse;
import com.cosmos.api.event.dto.ExpectedMoveResponse;
import com.cosmos.api.event.dto.MoveBacktestRowResponse;
import com.cosmos.api.event.dto.PathStepResponse;
import com.cosmos.api.event.dto.SignalCountsResponse;
import com.cosmos.api.event.error.EventErrorCode;
import com.cosmos.api.event.repository.EventQueryRepository;
import com.cosmos.api.event.repository.EventQueryRepository.AbnormalReturnRow;
import com.cosmos.api.event.repository.EventQueryRepository.BacktestRow;
import com.cosmos.api.event.repository.EventQueryRepository.CompanyEventRow;
import com.cosmos.api.event.repository.EventQueryRepository.CompanyKeyRow;
import com.cosmos.api.event.repository.EventQueryRepository.EventRow;
import com.cosmos.api.event.repository.EventQueryRepository.ExpectedMoveRow;
import com.cosmos.api.event.repository.EventQueryRepository.ImpactCompanyRow;
import com.cosmos.api.event.repository.EventQueryRepository.ImpactRow;
import com.cosmos.api.event.repository.EventQueryRepository.IndexCloseRow;
import com.cosmos.api.event.repository.EventQueryRepository.SignalCountRow;
import com.cosmos.api.event.repository.EventQueryRepository.SignalRow;
import com.cosmos.api.event.repository.EventSearchCondition;
import com.cosmos.api.event.service.EventPathParser.RawStep;
import com.cosmos.api.event.type.CompanyPosition;
import com.cosmos.api.event.type.NewsType;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import com.cosmos.api.global.response.CursorPageResponse;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Instant;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
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

    /** 투자 기간 → 딱지가 기준으로 삼는 거래일 수. SHORT 3거래일, LONG 20거래일(nexus/nexus/cases/reaction.py HORIZONS). */
    static final Map<String, Integer> HORIZON_DAYS = Map.of("SHORT", 3, "LONG", 20);

    /** 주가 차트 범위: 이벤트 거래일 앞 5거래일(offset -5 ~ -1)과 당일부터 21거래일(offset 0 ~ +20). */
    static final int CHART_DAYS_BEFORE = 5;
    static final int CHART_DAYS_FROM = 21;

    /** 차트 비교 지수가 있는 시장. 해외 상장사는 지수가 없어 차트를 그리지 않는다. */
    static final Set<String> BENCHMARKS = Set.of("KOSPI", "KOSDAQ");

    private static final int RETURN_SCALE = 8;
    private static final int RATE_SCALE = 6;

    private final EventQueryRepository eventRepository;
    private final CompanyFundamentalQueryRepository fundamentalRepository;
    private final TimeIdCursorCodec cursorCodec;
    private final EventPathParser pathParser;

    public EventService(EventQueryRepository eventRepository, CompanyFundamentalQueryRepository fundamentalRepository,
            TimeIdCursorCodec cursorCodec, EventPathParser pathParser) {
        this.eventRepository = eventRepository;
        this.fundamentalRepository = fundamentalRepository;
        this.cursorCodec = cursorCodec;
        this.pathParser = pathParser;
    }

    public CursorPageResponse<EventSummaryResponse> findEvents(
            String valueChain,
            UUID companyId,
            String newsType,
            boolean hasInferred,
            String cursor,
            int size
    ) {
        validateSize(size);
        validateValueChain(valueChain);
        if (newsType != null && !NewsType.isKnown(newsType)) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        TimeIdCursor decoded = cursorCodec.decode(cursor);
        List<EventRow> rows = eventRepository.findEvents(
                new EventSearchCondition(valueChain, companyId, newsType, hasInferred), decoded, size + 1);

        boolean hasNext = rows.size() > size;
        List<EventRow> pageRows = hasNext ? rows.subList(0, size) : rows;
        List<UUID> eventIds = pageRows.stream().map(EventRow::eventId).toList();
        Map<UUID, List<EventCompanyRefResponse>> reported = eventRepository.findReportedCompanies(eventIds);
        Map<UUID, SignalCountsResponse> counts = signalCounts(eventIds);
        List<EventSummaryResponse> items = pageRows.stream()
                .map(row -> summary(row, reported.getOrDefault(row.eventId(), List.of()), counts.get(row.eventId())))
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
                        Collectors.mapping(EventService::abnormalReturn, Collectors.toList())));
        List<SignalRow> signalRows = eventRepository.findSignals(eventId);
        Map<UUID, List<SignalRow>> signals = signalRows.stream()
                .collect(Collectors.groupingBy(SignalRow::companyId, LinkedHashMap::new, Collectors.toList()));
        List<UUID> companyIds = impacts.stream().map(ImpactRow::companyId).toList();
        Map<UUID, CompanyFundamentalResponse> fundamentals = fundamentalRepository.findByCompanyIds(companyIds);
        Map<UUID, List<ExpectedMoveResponse>> moves = eventRepository.findExpectedMoves(event.valueChain(), companyIds)
                .stream()
                .collect(Collectors.groupingBy(ExpectedMoveRow::companyId, LinkedHashMap::new,
                        Collectors.mapping(EventService::expectedMove, Collectors.toList())));

        Map<UUID, List<RawStep>> paths = new LinkedHashMap<>();
        impacts.forEach(i -> paths.put(i.companyId(), pathParser.parse(i.pathJson())));
        Map<String, CompanyKeyRow> companies = eventRepository.findCompaniesByKeys(paths.values().stream()
                .flatMap(List::stream).flatMap(s -> Stream.of(s.src(), s.dst())).filter(Objects::nonNull)
                .collect(Collectors.toSet()));
        Set<UUID> reported = new HashSet<>();
        impacts.stream().filter(i -> "REPORTED".equals(i.basis())).forEach(i -> reported.add(i.companyId()));

        List<EventNodeResponse> nodes = impacts.stream().map(i -> {
            List<PathStepResponse> steps = paths.get(i.companyId()).stream()
                    .map(s -> step(s, companies, event.dateline())).toList();
            UUID anchor = "INFERRED".equals(i.basis()) && !steps.isEmpty() ? anchorOf(steps.getFirst(), reported) : null;
            List<SignalRow> own = signals.getOrDefault(i.companyId(), List.of());
            String position = positionOf(own);
            return new EventNodeResponse(i.companyId(), i.name(), i.stockCode(), i.market(), i.basis(), i.hop(),
                    i.score(), i.status(), i.holdReason(), i.impactDirection(), i.impactMagnitude(), i.confidence(), anchor,
                    steps,
                    returns.getOrDefault(i.companyId(), List.of()),
                    position, CompanyPosition.labelOf(position),
                    own.stream().map(EventService::signal).toList(),
                    moves.getOrDefault(i.companyId(), List.of()),
                    fundamentals.get(i.companyId()));
        }).toList();
        List<EventCompanyRefResponse> reportedCompanies = impacts.stream()
                .filter(i -> "REPORTED".equals(i.basis()))
                .map(i -> new EventCompanyRefResponse(i.companyId(), i.name(), i.stockCode(), i.market()))
                .toList();
        SignalTally tally = new SignalTally();
        signalRows.forEach(s -> tally.add(s.horizon(), s.label(), 1));
        return new EventMapResponse(summary(event, reportedCompanies, tally.toResponse()), nodes);
    }

    /**
     * 뉴스 전후 주가 차트(F7). 기업 시장 지수의 거래일을 달력으로 쓰고, offset 0은 이벤트 거래일(그날이 달력에 없으면
     * 그 뒤 첫 거래일)이다. 수익률은 offset -1 종가 대비 누적이며 종가가 없는 날은 null이다.
     */
    public EventChartResponse findChart(UUID eventId, UUID companyId) {
        EventRow event = eventRepository.findEvent(eventId)
                .orElseThrow(() -> new BusinessException(EventErrorCode.EVENT_NOT_FOUND));
        ImpactCompanyRow company = eventRepository.findImpactCompany(eventId, companyId)
                .orElseThrow(() -> new BusinessException(EventErrorCode.EVENT_COMPANY_NOT_FOUND));
        LocalDate tradingDate = event.tradingDate();
        String benchmark = company.market();
        if (tradingDate == null || benchmark == null || !BENCHMARKS.contains(benchmark)) {
            throw new BusinessException(EventErrorCode.EVENT_CHART_UNAVAILABLE);
        }

        List<IndexCloseRow> calendar = eventRepository.findIndexCalendar(
                benchmark, tradingDate, CHART_DAYS_BEFORE, CHART_DAYS_FROM);
        if (calendar.isEmpty()) {
            return new EventChartResponse(eventId, companyId, tradingDate, benchmark, List.of());
        }
        Map<LocalDate, BigDecimal> closes = eventRepository.findDailyCloses(
                companyId, calendar.getFirst().tradingDate(), calendar.getLast().tradingDate());
        int zero = (int) calendar.stream().filter(c -> c.tradingDate().isBefore(tradingDate)).count();
        IndexCloseRow base = zero > 0 ? calendar.get(zero - 1) : null;
        BigDecimal baseClose = base == null ? null : closes.get(base.tradingDate());
        BigDecimal baseIndex = base == null ? null : base.close();

        List<ChartPointResponse> points = new ArrayList<>(calendar.size());
        for (int k = 0; k < calendar.size(); k++) {
            IndexCloseRow day = calendar.get(k);
            BigDecimal close = closes.get(day.tradingDate());
            BigDecimal stockReturn = cumulativeReturn(close, baseClose);
            BigDecimal indexReturn = cumulativeReturn(day.close(), baseIndex);
            BigDecimal abnormal = stockReturn == null || indexReturn == null ? null : stockReturn.subtract(indexReturn);
            points.add(new ChartPointResponse(k - zero, day.tradingDate(), close, day.close(), stockReturn,
                    indexReturn, abnormal));
        }
        return new EventChartResponse(eventId, companyId, tradingDate, benchmark, points);
    }

    /**
     * 방향 딱지 적중률(F11)과 예상 변동폭 검증. 파이프라인이 학습 기간 뒤 뉴스로 잰 값을 그대로 돌려주고 비율만 계산한다.
     */
    public BacktestResponse findBacktest(String valueChain) {
        validateValueChain(valueChain);
        List<BacktestRow> rows = eventRepository.findBacktest(valueChain);
        List<MoveBacktestRowResponse> moveRows = eventRepository.findMoveBacktest(valueChain).stream()
                .map(r -> new MoveBacktestRowResponse(r.horizon(), HORIZON_DAYS.getOrDefault(r.horizon(), 0),
                        r.grade(), r.caseCount(), r.predictedMove(), r.realizedMove()))
                .toList();
        if (rows.isEmpty()) {
            return new BacktestResponse(valueChain, null, null, null, List.of(), moveRows);
        }
        BacktestRow first = rows.getFirst();
        List<BacktestRowResponse> items = rows.stream().map(r -> new BacktestRowResponse(
                r.horizon(), r.newsType(), NewsType.labelOf(r.newsType()), r.caseCount(), r.labeledCount(),
                r.hitCount(),
                r.labeledCount() == 0 ? null : BigDecimal.valueOf(r.hitCount())
                        .divide(BigDecimal.valueOf(r.labeledCount()), RATE_SCALE, RoundingMode.HALF_UP),
                r.upLabels(), r.downLabels(), r.baseDownShare(),
                r.baseDownShare() == null ? null : r.baseDownShare().max(BigDecimal.ONE.subtract(r.baseDownShare()))))
                .toList();
        return new BacktestResponse(valueChain, first.trainEnd(), first.testStart(), first.testEnd(), items, moveRows);
    }

    /** 기업 한 곳의 과거 뉴스와 그때 주가 반응(F13). 이 기업이 OK 영향으로 들어간 이벤트만, 최신순. */
    public CursorPageResponse<CompanyHistoryItemResponse> findCompanyHistory(UUID companyId, String cursor, int size) {
        validateSize(size);
        if (companyId == null) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
        TimeIdCursor decoded = cursorCodec.decode(cursor);
        List<CompanyEventRow> rows = eventRepository.findCompanyEvents(companyId, decoded, size + 1);

        boolean hasNext = rows.size() > size;
        List<CompanyEventRow> pageRows = hasNext ? rows.subList(0, size) : rows;
        List<UUID> eventIds = pageRows.stream().map(r -> r.event().eventId()).toList();
        Map<UUID, List<EventCompanyRefResponse>> reported = eventRepository.findReportedCompanies(eventIds);
        Map<UUID, SignalCountsResponse> counts = signalCounts(eventIds);
        Map<UUID, List<SignalRow>> signals = eventRepository.findCompanySignals(companyId, eventIds).stream()
                .collect(Collectors.groupingBy(SignalRow::eventId, LinkedHashMap::new, Collectors.toList()));
        Map<UUID, List<AbnormalReturnResponse>> returns = eventRepository.findCompanyAbnormalReturns(companyId, eventIds)
                .stream()
                .collect(Collectors.groupingBy(AbnormalReturnRow::eventId, LinkedHashMap::new,
                        Collectors.mapping(EventService::abnormalReturn, Collectors.toList())));

        List<CompanyHistoryItemResponse> items = pageRows.stream().map(r -> {
            UUID eventId = r.event().eventId();
            List<SignalRow> own = signals.getOrDefault(eventId, List.of());
            String position = positionOf(own);
            return new CompanyHistoryItemResponse(
                    summary(r.event(), reported.getOrDefault(eventId, List.of()), counts.get(eventId)),
                    r.basis(), position, CompanyPosition.labelOf(position),
                    own.stream().map(EventService::signal).toList(),
                    returns.getOrDefault(eventId, List.of()));
        }).toList();

        String nextCursor = null;
        if (hasNext && !pageRows.isEmpty()) {
            EventRow last = pageRows.getLast().event();
            nextCursor = cursorCodec.encode(new TimeIdCursor(last.dateline(), last.eventId()));
        }
        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    private static void validateSize(int size) {
        if (size < 1 || size > 100) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
    }

    private static void validateValueChain(String valueChain) {
        // 밸류체인은 필수다: 없으면 (value_chain, dateline) 인덱스를 못 타고 이벤트 전체를 훑는다
        if (valueChain == null || !VALUE_CHAINS.contains(valueChain)) {
            throw new BusinessException(CommonErrorCode.VALIDATION_FAILED);
        }
    }

    private Map<UUID, SignalCountsResponse> signalCounts(List<UUID> eventIds) {
        Map<UUID, SignalTally> tallies = new HashMap<>();
        for (SignalCountRow row : eventRepository.findSignalCounts(eventIds)) {
            tallies.computeIfAbsent(row.eventId(), k -> new SignalTally()).add(row.horizon(), row.label(), row.count());
        }
        Map<UUID, SignalCountsResponse> result = new HashMap<>();
        tallies.forEach((eventId, tally) -> result.put(eventId, tally.toResponse()));
        return result;
    }

    /** 기간마다 위치가 같으므로(위치는 이벤트·기업으로 정해진다) 첫 딱지의 위치를 쓴다. 딱지가 없으면 null. */
    private static String positionOf(List<SignalRow> signals) {
        return signals.isEmpty() ? null : signals.getFirst().position();
    }

    private static CompanySignalResponse signal(SignalRow s) {
        return new CompanySignalResponse(s.horizon(), HORIZON_DAYS.getOrDefault(s.horizon(), 0), s.label(),
                s.holdReason(), s.caseScope(), s.caseCount(), s.sameShare(), s.meanAr(), s.downShare());
    }

    private static ExpectedMoveResponse expectedMove(ExpectedMoveRow m) {
        return new ExpectedMoveResponse(m.horizon(), HORIZON_DAYS.getOrDefault(m.horizon(), 0), m.expectedMove(),
                m.caseCount(), m.grade());
    }

    private static AbnormalReturnResponse abnormalReturn(AbnormalReturnRow r) {
        return new AbnormalReturnResponse(r.windowDays(), r.benchmark(), r.stockReturn(), r.benchmarkReturn(),
                r.abnormalReturn());
    }

    /** close / base - 1. 어느 쪽이든 없거나 기준가가 0이면 null. */
    private static BigDecimal cumulativeReturn(BigDecimal close, BigDecimal base) {
        if (close == null || base == null || base.signum() == 0) {
            return null;
        }
        return close.divide(base, RETURN_SCALE, RoundingMode.HALF_UP).subtract(BigDecimal.ONE);
    }

    /** 근거 조각은 이벤트 시각(asOf) 이전 기사부터 보여 준다. */
    private PathStepResponse step(RawStep s, Map<String, CompanyKeyRow> companies, Instant asOf) {
        CompanyKeyRow source = s.src() == null ? null : companies.get(s.src());
        CompanyKeyRow target = s.dst() == null ? null : companies.get(s.dst());
        boolean withEvidence = "NEWS".equals(s.source()) && source != null && target != null && s.rel() != null;
        return new PathStepResponse(
                source == null ? null : source.companyId(), source == null ? null : source.name(),
                target == null ? null : target.companyId(), target == null ? null : target.name(),
                s.rel(), s.source(), s.strength(),
                withEvidence ? eventRepository.findSnippets(source.companyId(), target.companyId(), s.rel(), asOf,
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

    static EventSummaryResponse summary(EventRow row, List<EventCompanyRefResponse> reportedCompanies,
            SignalCountsResponse signalCounts) {
        return new EventSummaryResponse(
                row.eventId(), row.newsId(), row.title(), row.summary(), row.publisher(), row.originalUrl(),
                row.dateline(), row.tradingDate(), row.valueChain(), row.categories(), reportedCompanies,
                row.inferredCount(), row.holdCount(), row.newsType(), NewsType.labelOf(row.newsType()),
                signalCounts == null ? SignalCountsResponse.empty() : signalCounts);
    }

    /** 투자 기간 × 딱지 개수 집계. 모르는 기간·딱지 값은 세지 않는다. */
    private static final class SignalTally {

        private final int[] shortTerm = new int[3];
        private final int[] longTerm = new int[3];

        void add(String horizon, String label, int count) {
            int[] target = "SHORT".equals(horizon) ? shortTerm : "LONG".equals(horizon) ? longTerm : null;
            int index = switch (label == null ? "" : label) {
                case "UP" -> 0;
                case "DOWN" -> 1;
                case "HOLD" -> 2;
                default -> -1;
            };
            if (target != null && index >= 0) {
                target[index] += count;
            }
        }

        SignalCountsResponse toResponse() {
            return new SignalCountsResponse(
                    new SignalCountsResponse.HorizonCount(shortTerm[0], shortTerm[1], shortTerm[2]),
                    new SignalCountsResponse.HorizonCount(longTerm[0], longTerm[1], longTerm[2]));
        }
    }
}
