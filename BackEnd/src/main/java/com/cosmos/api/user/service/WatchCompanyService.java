package com.cosmos.api.user.service;

import com.cosmos.api.company.entity.Company;
import com.cosmos.api.company.entity.CompanyIndustry;
import com.cosmos.api.company.entity.Industry;
import com.cosmos.api.company.error.CompanyErrorCode;
import com.cosmos.api.company.repository.CompanyIndustryRepository;
import com.cosmos.api.company.repository.CompanyRepository;
import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.user.dto.WatchCompanyItemResponse;
import com.cosmos.api.user.dto.WatchCompanyResponse;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.entity.UserWatchCompany;
import com.cosmos.api.user.entity.UserWatchCompanyId;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
import com.cosmos.api.user.repository.UserWatchCompanyRepository;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 관심 기업 등록·해제·목록. 사용자는 자기 관심 목록만 다룰 수 있다(토큰의 사용자 번호 기준). */
@Service
@RequiredArgsConstructor
public class WatchCompanyService {

    private final UserWatchCompanyRepository watchRepository;
    private final CompanyRepository companyRepository;
    private final CompanyIndustryRepository companyIndustryRepository;
    private final UserRepository userRepository;
    private final TimeIdCursorCodec cursorCodec;

    /** 내 관심 기업 목록 (등록 최신순, 커서 페이지). */
    @Transactional(readOnly = true)
    public CursorPageResponse<WatchCompanyItemResponse> findMyWatchCompanies(Long userId, String cursor, int size) {
        TimeIdCursor from = cursorCodec.decode(cursor);
        // 다음 페이지가 있는지 알려면 요청보다 1개 더 읽어 본다
        PageRequest limit = PageRequest.of(0, size + 1);
        List<UserWatchCompany> rows = (from == null)
                ? watchRepository.findFirstPage(userId, limit)
                : watchRepository.findNextPage(userId, from.at(), from.id(), limit);

        boolean hasNext = rows.size() > size;
        List<UserWatchCompany> pageRows = hasNext ? rows.subList(0, size) : rows;

        Map<UUID, Industry> primaryIndustries = findPrimaryIndustries(pageRows);
        List<WatchCompanyItemResponse> items = pageRows.stream()
                .map(watch -> WatchCompanyItemResponse.from(
                        watch, primaryIndustries.get(watch.getId().getCompanyId())))
                .toList();

        String nextCursor = null;
        if (hasNext) {
            UserWatchCompany last = pageRows.get(pageRows.size() - 1);
            nextCursor = cursorCodec.encode(new TimeIdCursor(last.getCreatedAt(), last.getId().getCompanyId()));
        }
        return CursorPageResponse.of(items, nextCursor, hasNext);
    }

    // 이 페이지에 나오는 기업들의 대표 산업을 한 번의 조회로 모아 둔다 (기업마다 따로 조회하지 않기 위해)
    private Map<UUID, Industry> findPrimaryIndustries(List<UserWatchCompany> rows) {
        if (rows.isEmpty()) {
            return Map.of();
        }
        List<UUID> companyIds = rows.stream().map(watch -> watch.getId().getCompanyId()).toList();
        return companyIndustryRepository.findPrimaryWithIndustryByCompanyIds(companyIds).stream()
                .collect(Collectors.toMap(
                        ci -> ci.getCompany().getCompanyId(),
                        CompanyIndustry::getIndustry,
                        (first, ignored) -> first)); // 대표 산업이 둘 이상 지정된 데이터가 있어도 하나만 쓴다
    }

    /** 등록. 없는 기업이면 404, 이미 등록한 기업이면 409. */
    @Transactional
    public WatchCompanyResponse register(Long userId, UUID companyId) {
        Company company = companyRepository.findActiveById(companyId)
                .orElseThrow(() -> new BusinessException(CompanyErrorCode.COMPANY_NOT_FOUND));
        User user = findActiveUser(userId);

        if (watchRepository.existsById(new UserWatchCompanyId(userId, companyId))) {
            throw new BusinessException(UserErrorCode.WATCH_COMPANY_DUPLICATED);
        }

        UserWatchCompany saved = watchRepository.save(UserWatchCompany.create(user, company));
        return WatchCompanyResponse.from(saved);
    }

    /** 해제. 등록돼 있지 않아도 조용히 성공한다(멱등). 없는 기업 번호여도 마찬가지다. */
    @Transactional
    public void unregister(Long userId, UUID companyId) {
        watchRepository.findById(new UserWatchCompanyId(userId, companyId))
                .ifPresent(watchRepository::delete);
    }

    // 토큰은 30분간 살아 있어서, 그 사이 탈퇴한 회원의 토큰이 들어올 수 있다
    private User findActiveUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(AuthErrorCode.TOKEN_INVALID));
    }
}
