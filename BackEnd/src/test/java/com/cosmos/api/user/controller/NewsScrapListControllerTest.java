package com.cosmos.api.user.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.Role;
import com.cosmos.api.user.repository.UserRepository;
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
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 내 뉴스 스크랩 목록 조회 API 테스트.
 * 스크랩 최신순 정렬, 커서로 이어보기, 내 것만 보이는지가 핵심이라 그 세 가지를 중심으로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class NewsScrapListControllerTest {

    private static final UUID SOURCE_ID = UUID.fromString("66000000-0000-0000-0000-000000000030");
    private static final UUID COMPANY_ID = UUID.fromString("66000000-0000-0000-0000-000000000001");
    // 뉴스 번호를 스크랩 순서와 같은 오름차순으로 두어, 같은 시각에 스크랩돼도 "마지막 스크랩이 맨 위" 규칙이 유지되게 한다
    private static final UUID NEWS_A = UUID.fromString("66000000-0000-0000-0000-00000000002a");
    private static final UUID NEWS_B = UUID.fromString("66000000-0000-0000-0000-00000000002b");
    private static final UUID NEWS_C = UUID.fromString("66000000-0000-0000-0000-00000000002c");

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
    JwtTokenProvider jwtTokenProvider;

    private String myToken;

    /** 노출 대상 뉴스 3개(A, B, C)와 회원 한 명을 준비한다. */
    @BeforeEach
    void setUp() throws Exception {
        jdbcTemplate.update("INSERT INTO data_source (source_id, name, source_type) VALUES (?, '스크랩 목록 테스트 소스', 'NEWS')",
                SOURCE_ID);
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, '스크랩목록기업', 'ACTIVE')",
                COMPANY_ID);
        insertNews(NEWS_A, "뉴스A", "2026-09-01T02:00:00Z", 'k');
        insertNews(NEWS_B, "뉴스B", "2026-09-02T02:00:00Z", 'l');
        insertNews(NEWS_C, "뉴스C", "2026-09-03T02:00:00Z", 'm');

        myToken = signupAndToken("scraplist@test.com", "스크랩목록테스터");
    }

    private void insertNews(UUID newsId, String title, String publishedAt, char hashCharacter) {
        jdbcTemplate.update("""
                INSERT INTO source_document
                    (document_id, source_id, document_type, title, summary, original_url, published_at, status)
                VALUES (?, ?, 'NEWS', ?, '요약', ?, CAST(? AS TIMESTAMPTZ), 'ANALYZED')
                """, newsId, SOURCE_ID, title, "https://example.com/" + newsId, publishedAt);
        jdbcTemplate.update("""
                INSERT INTO news_article (document_id, publisher, canonical_url, canonical_url_hash)
                VALUES (?, '예시경제', ?, ?)
                """, newsId, "https://example.com/" + newsId, String.valueOf(hashCharacter).repeat(64));
        jdbcTemplate.update("""
                INSERT INTO company_document
                    (document_id, company_id, relevance_score, sentiment, confidence, model_version, is_service_visible)
                VALUES (?, ?, 0.9, 'POSITIVE', 0.9, 'test-v1', TRUE)
                """, newsId, COMPANY_ID);
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

    private void scrap(String token, UUID newsId) throws Exception {
        mockMvc.perform(post("/api/users/me/scraps/{newsId}", newsId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isCreated());
    }

    // 상황: A, B, C 순서로 스크랩한 뒤 목록 조회
    // 기대: 200 + 스크랩 최신순(C, B, A). 발행일이 아니라 "스크랩한 순서"가 기준이다
    @Test
    @DisplayName("스크랩 목록은 스크랩 최신순으로 나온다")
    void list_latest_first() throws Exception {
        scrap(myToken, NEWS_A);
        scrap(myToken, NEWS_B);
        scrap(myToken, NEWS_C);

        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(3))
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_C.toString()))
                .andExpect(jsonPath("$.items[0].title").value("뉴스C"))
                .andExpect(jsonPath("$.items[0].summary").value("요약"))
                .andExpect(jsonPath("$.items[0].publisher").value("예시경제"))
                .andExpect(jsonPath("$.items[0].originalUrl").value("https://example.com/" + NEWS_C))
                .andExpect(jsonPath("$.items[0].publishedAt").value("2026-09-03T02:00:00Z"))
                .andExpect(jsonPath("$.items[0].scrappedAt").isString())
                .andExpect(jsonPath("$.items[2].newsId").value(NEWS_A.toString()))
                .andExpect(jsonPath("$.hasNext").value(false))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
    }

    // 상황: 3개를 스크랩하고 size=2로 첫 페이지를 받은 뒤, 받은 커서로 다음 페이지 요청
    // 기대: 첫 페이지 2개(hasNext=true) → 다음 페이지 1개(hasNext=false), 중복 없이 이어짐
    @Test
    @DisplayName("커서로 다음 페이지를 이어서 볼 수 있다")
    void list_paging() throws Exception {
        scrap(myToken, NEWS_A);
        scrap(myToken, NEWS_B);
        scrap(myToken, NEWS_C);

        MvcResult first = mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_C.toString()))
                .andExpect(jsonPath("$.items[1].newsId").value(NEWS_B.toString()))
                .andExpect(jsonPath("$.hasNext").value(true))
                .andReturn();

        String cursor = objectMapper.readTree(first.getResponse().getContentAsString())
                .get("nextCursor").asString();

        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "2")
                        .param("cursor", cursor))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_A.toString()))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 내가 A를, 다른 사용자가 B를 스크랩한 뒤 내 목록 조회
    // 기대: 내 것(A)만 보인다
    @Test
    @DisplayName("다른 사용자의 스크랩은 내 목록에 섞이지 않는다")
    void list_only_mine() throws Exception {
        scrap(myToken, NEWS_A);
        String otherToken = signupAndToken("other-scraplist@test.com", "다른스크랩목록");
        scrap(otherToken, NEWS_B);

        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].newsId").value(NEWS_A.toString()));
    }

    // 상황: 아무것도 스크랩하지 않은 상태에서 조회
    // 기대: 200 + 빈 배열 (오류가 아니다)
    @Test
    @DisplayName("스크랩한 뉴스가 없으면 빈 목록을 돌려준다")
    void list_empty() throws Exception {
        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(0))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 커서 자리에 아무 문자열을 넣어 요청
    // 기대: 400 INVALID_CURSOR
    @Test
    @DisplayName("커서 형식이 잘못되면 400 INVALID_CURSOR")
    void list_invalid_cursor() throws Exception {
        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("cursor", "broken-cursor"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
    }

    // 상황: size에 허용 범위(1~100)를 벗어난 값을 넣어 요청
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("size가 허용 범위를 벗어나면 400")
    void list_invalid_size() throws Exception {
        mockMvc.perform(get("/api/users/me/scraps")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "101"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 토큰 없이 목록 조회
    // 기대: 401 — 내 목록은 로그인 필요
    @Test
    @DisplayName("토큰 없이 스크랩 목록을 볼 수 없다")
    void list_without_token() throws Exception {
        mockMvc.perform(get("/api/users/me/scraps"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }
}
