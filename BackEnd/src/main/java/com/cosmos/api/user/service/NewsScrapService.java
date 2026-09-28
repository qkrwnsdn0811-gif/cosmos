package com.cosmos.api.user.service;

import com.cosmos.api.global.cursor.TimeIdCursor;
import com.cosmos.api.global.cursor.TimeIdCursorCodec;
import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthErrorCode;
import com.cosmos.api.news.error.NewsErrorCode;
import com.cosmos.api.news.repository.NewsQueryRepository;
import com.cosmos.api.user.dto.NewsScrapItemResponse;
import com.cosmos.api.user.dto.NewsScrapResponse;
import com.cosmos.api.user.entity.User;
import com.cosmos.api.user.entity.UserScrap;
import com.cosmos.api.user.entity.UserScrapId;
import com.cosmos.api.user.error.UserErrorCode;
import com.cosmos.api.user.repository.UserRepository;
import com.cosmos.api.user.repository.UserScrapQueryRepository;
import com.cosmos.api.user.repository.UserScrapQueryRepository.ScrapRow;
import com.cosmos.api.user.repository.UserScrapRepository;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 뉴스 스크랩 등록·해제·목록. 사용자는 자기 스크랩만 다룰 수 있다(토큰의 사용자 번호 기준). */
@Service
@RequiredArgsConstructor
public class NewsScrapService {

    private final UserScrapRepository scrapRepository;
    private final UserScrapQueryRepository scrapQueryRepository;
    private final NewsQueryRepository newsQueryRepository;
    private final UserRepository userRepository;
    private final TimeIdCursorCodec cursorCodec;

    /** 내 스크랩 목록 (스크랩 최신순, 커서 페이지). */
    @Transactional(readOnly = true)
    public CursorPageResponse<NewsScrapItemResponse> findMyScraps(Long userId, String cursor, int size) {
        TimeIdCursor from = cursorCodec.decode(cursor);
        // 다음 페이지가 있는지 알려면 요청보다 1개 더 읽어 본다
        List<ScrapRow> rows = scrapQueryRepository.findPage(
                userId,
                from == null ? null : from.at(),
                from == null ? null : from.id(),
                size + 1);

        boolean hasNext = rows.size() > size;
        List<ScrapRow> pageRows = hasNext ? rows.subList(0, size) : rows;

        String nextCursor = null;
        if (hasNext) {
            ScrapRow last = pageRows.get(pageRows.size() - 1);
            nextCursor = cursorCodec.encode(new TimeIdCursor(last.scrappedAt(), last.newsId()));
        }
        return CursorPageResponse.of(pageRows.stream().map(NewsScrapItemResponse::from).toList(), nextCursor, hasNext);
    }

    /**
     * 등록. 뉴스가 없거나 서비스 노출 대상이 아니면 404, 이미 스크랩했으면 409.
     * "노출 대상" 판단은 뉴스 상세 조회와 같은 기준을 쓰기 위해 뉴스 조회 저장소를 그대로 사용한다.
     */
    @Transactional
    public NewsScrapResponse register(Long userId, UUID newsId) {
        // 사용자 번호 없이 조회해 "노출 대상인지"만 확인한다. 중복 여부는 아래에서 JPA로 따로 본다
        // (JPA 저장 직후 아직 DB에 안 내려간 행을 SQL 조회는 못 보기 때문)
        newsQueryRepository.findDetail(newsId, null)
                .orElseThrow(() -> new BusinessException(NewsErrorCode.NEWS_NOT_FOUND));
        if (scrapRepository.existsById(new UserScrapId(userId, newsId))) {
            throw new BusinessException(UserErrorCode.NEWS_SCRAP_DUPLICATED);
        }
        User user = findActiveUser(userId);

        // 목록 조회는 SQL로 읽으므로, 저장 즉시 DB에 내려보내 같은 트랜잭션 안에서도 바로 보이게 한다
        UserScrap saved = scrapRepository.saveAndFlush(UserScrap.create(user, newsId));
        return NewsScrapResponse.from(saved);
    }

    /** 해제. 스크랩돼 있지 않아도 조용히 성공한다(멱등). 없는 뉴스 번호여도 마찬가지다. */
    @Transactional
    public void unregister(Long userId, UUID newsId) {
        scrapRepository.findById(new UserScrapId(userId, newsId))
                .ifPresent(scrapRepository::delete);
    }

    // 토큰은 30분간 살아 있어서, 그 사이 탈퇴한 회원의 토큰이 들어올 수 있다
    private User findActiveUser(Long userId) {
        return userRepository.findById(userId)
                .orElseThrow(() -> new BusinessException(AuthErrorCode.TOKEN_INVALID));
    }
}
