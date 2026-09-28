package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.Role;
import com.cosmos.api.user.entity.UserScrapId;
import com.cosmos.api.user.repository.UserRepository;
import com.cosmos.api.user.repository.UserScrapRepository;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import com.cosmos.api.user.service.EmailVerificationStore;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 뉴스 스크랩 등록·해제 API 테스트.
 * "노출 대상 뉴스만 스크랩되는지", "중복 처리", "해제는 멱등인지", "내 것만 건드리는지"를 중심으로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class NewsScrapControllerTest {

    private static final UUID SOURCE_ID = UUID.fromString("64000000-0000-0000-0000-000000000030");
    private static final UUID COMPANY_ID = UUID.fromString("64000000-0000-0000-0000-000000000001");
    private static final UUID NEWS_VISIBLE = UUID.fromString("64000000-0000-0000-0000-000000000020");
    private static final UUID NEWS_HIDDEN = UUID.fromString("64000000-0000-0000-0000-000000000021");
    private static final UUID UNKNOWN_NEWS = UUID.fromString("64000000-0000-0000-0000-0000000000ff");

    @Autowired
    MockMvc mockMvc;
@Autowired    EmailVerificationStore emailVerificationStore; // 가입 전 이메일 인증을 마친 것으로 표시하기 위해

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    UserRepository userRepository;

    @Autowired
    UserScrapRepository scrapRepository;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    private Long myUserId;
    private String myToken;

    /**
     * 노출 대상 뉴스 하나(활성 기업에 연결, 서비스 노출 O)와 숨김 뉴스 하나(서비스 노출 X), 회원 한 명을 준비한다.
     * 뉴스는 source_document + news_article 두 테이블에 함께 들어가야 뉴스로 인정된다.
     */
    @BeforeEach
    void setUp() throws Exception {
        jdbcTemplate.update("INSERT INTO data_source (source_id, name, source_type) VALUES (?, '스크랩 테스트 소스', 'NEWS')",
                SOURCE_ID);
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, '스크랩테스트기업', 'ACTIVE')",
                COMPANY_ID);
        insertNews(NEWS_VISIBLE, "노출 뉴스", 'x', true);
        insertNews(NEWS_HIDDEN, "숨김 뉴스", 'y', false);

        myToken = signupAndToken("scrap@test.com", "스크랩테스터");
        myUserId = userRepository.findByEmail("scrap@test.com").orElseThrow().getUserId();
    }

    private void insertNews(UUID newsId, String title, char hashCharacter, boolean visible) {
        jdbcTemplate.update("""
                INSERT INTO source_document
                    (document_id, source_id, document_type, title, summary, original_url, published_at, status)
                VALUES (?, ?, 'NEWS', ?, '요약', ?, CAST('2026-09-07T02:00:00Z' AS TIMESTAMPTZ), 'ANALYZED')
                """, newsId, SOURCE_ID, title, "https://example.com/" + newsId);
        jdbcTemplate.update("""
                INSERT INTO news_article (document_id, publisher, canonical_url, canonical_url_hash)
                VALUES (?, '예시경제', ?, ?)
                """, newsId, "https://example.com/" + newsId, String.valueOf(hashCharacter).repeat(64));
        jdbcTemplate.update("""
                INSERT INTO company_document
                    (document_id, company_id, relevance_score, sentiment, confidence, model_version, is_service_visible)
                VALUES (?, ?, 0.9, 'POSITIVE', 0.9, 'test-v1', ?)
                """, newsId, COMPANY_ID, visible);
    }

    private String signupAndToken(String email, String nickname) throws Exception {
        emailVerificationStore.markVerified(email.toLowerCase()); // 인증 단계를 거친 것으로 처리
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new SignupRequest(email, "password1", nickname))))
                .andExpect(status().isCreated());
        Long userId = userRepository.findByEmail(email).orElseThrow().getUserId();
        return jwtTokenProvider.createAccessToken(userId, Role.ROLE_USER);
    }

    private ResultActions scrap(String token, Object newsId) throws Exception {
        return mockMvc.perform(post("/api/users/me/scraps/{newsId}", newsId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token));
    }

    private ResultActions unscrap(String token, Object newsId) throws Exception {
        return mockMvc.perform(delete("/api/users/me/scraps/{newsId}", newsId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token));
    }

    private boolean isScrapped(Long userId, UUID newsId) {
        return scrapRepository.existsById(new UserScrapId(userId, newsId));
    }

    // 상황: 로그인한 사용자가 노출 대상 뉴스를 스크랩
    // 기대: 201 + 뉴스 번호·스크랩 시각, DB에 내 스크랩으로 남는다
    @Test
    @DisplayName("뉴스를 스크랩하면 201과 스크랩 정보를 돌려준다")
    void scrap_success() throws Exception {
        scrap(myToken, NEWS_VISIBLE)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.newsId").value(NEWS_VISIBLE.toString()))
                .andExpect(jsonPath("$.scrappedAt").isString());

        assertThat(isScrapped(myUserId, NEWS_VISIBLE)).isTrue();
    }

    // 상황: 같은 뉴스를 두 번 스크랩
    // 기대: 두 번째는 409 NEWS_SCRAP_DUPLICATED
    @Test
    @DisplayName("이미 스크랩한 뉴스를 다시 스크랩하면 409")
    void scrap_duplicated() throws Exception {
        scrap(myToken, NEWS_VISIBLE).andExpect(status().isCreated());

        scrap(myToken, NEWS_VISIBLE)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("NEWS_SCRAP_DUPLICATED"));
    }

    // 상황: 존재하지 않는 뉴스 번호로 스크랩
    // 기대: 404 NEWS_NOT_FOUND
    @Test
    @DisplayName("없는 뉴스는 스크랩할 수 없다")
    void scrap_news_not_found() throws Exception {
        scrap(myToken, UNKNOWN_NEWS)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("NEWS_NOT_FOUND"));
    }

    // 상황: DB에는 있지만 서비스 노출 대상이 아닌(숨김) 뉴스를 스크랩
    // 기대: 없는 뉴스와 똑같이 404 (뉴스 상세 조회와 같은 기준)
    @Test
    @DisplayName("노출 대상이 아닌 뉴스는 없는 뉴스처럼 404")
    void scrap_hidden_news() throws Exception {
        scrap(myToken, NEWS_HIDDEN)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("NEWS_NOT_FOUND"));
    }

    // 상황: 뉴스 번호 자리에 UUID가 아닌 문자열
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("뉴스 번호 형식이 잘못되면 400")
    void scrap_invalid_news_id() throws Exception {
        scrap(myToken, "not-a-uuid")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 토큰 없이 스크랩 시도
    // 기대: 401 — 스크랩은 로그인 필요
    @Test
    @DisplayName("토큰 없이 스크랩할 수 없다")
    void scrap_without_token() throws Exception {
        mockMvc.perform(post("/api/users/me/scraps/{newsId}", NEWS_VISIBLE))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 스크랩한 뉴스를 해제
    // 기대: 204, DB에서 사라진다
    @Test
    @DisplayName("스크랩을 해제하면 204와 함께 목록에서 빠진다")
    void unscrap_success() throws Exception {
        scrap(myToken, NEWS_VISIBLE).andExpect(status().isCreated());

        unscrap(myToken, NEWS_VISIBLE).andExpect(status().isNoContent());

        assertThat(isScrapped(myUserId, NEWS_VISIBLE)).isFalse();
    }

    // 상황: 스크랩한 적 없는 뉴스(또는 없는 뉴스 번호)를 해제
    // 기대: 오류 없이 204 (멱등 — 두 번 눌러도 결과가 같다)
    @Test
    @DisplayName("스크랩하지 않은 뉴스를 해제해도 204")
    void unscrap_is_idempotent() throws Exception {
        unscrap(myToken, NEWS_VISIBLE).andExpect(status().isNoContent());
        unscrap(myToken, UNKNOWN_NEWS).andExpect(status().isNoContent());
    }

    // 상황: 내가 스크랩한 뉴스를 다른 사용자가 해제 시도
    // 기대: 그 사용자 목록에서만 빠질 뿐(원래 없음), 내 스크랩은 그대로 남는다
    @Test
    @DisplayName("다른 사용자의 스크랩에는 영향을 주지 않는다")
    void unscrap_does_not_touch_other_users() throws Exception {
        scrap(myToken, NEWS_VISIBLE).andExpect(status().isCreated());
        String otherToken = signupAndToken("other-scrap@test.com", "다른스크랩사람");

        unscrap(otherToken, NEWS_VISIBLE).andExpect(status().isNoContent());

        assertThat(isScrapped(myUserId, NEWS_VISIBLE)).isTrue();
    }
}
