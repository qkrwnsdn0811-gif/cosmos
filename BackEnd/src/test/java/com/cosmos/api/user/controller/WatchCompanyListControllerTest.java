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
 * 내 관심 기업 목록 조회 API 테스트.
 * 등록 최신순 정렬, 커서로 이어보기, 내 것만 보이는지, 대표 산업 표시가 핵심이라 그 네 가지를 중심으로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WatchCompanyListControllerTest {

    // 기업 번호를 등록 순서와 같은 오름차순으로 두어, 같은 시각에 등록돼도 "마지막 등록이 맨 위" 규칙이 유지되게 한다
    private static final UUID COMPANY_A = UUID.fromString("62000000-0000-0000-0000-00000000000a");
    private static final UUID COMPANY_B = UUID.fromString("62000000-0000-0000-0000-00000000000b");
    private static final UUID COMPANY_C = UUID.fromString("62000000-0000-0000-0000-00000000000c");
    private static final UUID COMPANY_INACTIVE = UUID.fromString("62000000-0000-0000-0000-00000000000d");
    private static final UUID INDUSTRY_SEMI = UUID.fromString("62000000-0000-0000-0000-000000000101");
    private static final UUID INDUSTRY_AUTO = UUID.fromString("62000000-0000-0000-0000-000000000102");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    UserRepository userRepository;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    private String myToken;

    /**
     * 기업 A(반도체 대표, 자동차 부수), B(대표 산업 없음), C(자동차 대표), 비활성 기업 하나와 회원 한 명을 준비한다.
     * 비활성 기업은 활성일 때 등록한 뒤 상태를 바꿔, 목록에서 빠지는지 볼 수 있게 한다.
     */
    @BeforeEach
    void setUp() throws Exception {
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, ?)", INDUSTRY_SEMI, "목록테스트반도체");
        jdbcTemplate.update("INSERT INTO industry (industry_id, name) VALUES (?, ?)", INDUSTRY_AUTO, "목록테스트자동차");

        insertCompany(COMPANY_A, "목록기업A", "000010", "KOSPI");
        insertCompany(COMPANY_B, "목록기업B", "000020", "KOSPI");
        insertCompany(COMPANY_C, "목록기업C", "AAPL0", "NASDAQ");
        insertCompany(COMPANY_INACTIVE, "곧비활성기업", "000040", "KOSPI");

        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)",
                COMPANY_A, INDUSTRY_SEMI);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, FALSE)",
                COMPANY_A, INDUSTRY_AUTO);
        jdbcTemplate.update("INSERT INTO company_industry (company_id, industry_id, is_primary) VALUES (?, ?, TRUE)",
                COMPANY_C, INDUSTRY_AUTO);

        myToken = signupAndToken("watchlist@test.com", "목록관심테스터");
    }

    private void insertCompany(UUID companyId, String name, String stockCode, String market) {
        jdbcTemplate.update(
                "INSERT INTO company (company_id, name, stock_code, market, status) VALUES (?, ?, ?, ?, 'ACTIVE')",
                companyId, name, stockCode, market);
    }

    private String signupAndToken(String email, String nickname) throws Exception {
        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new SignupRequest(email, "password1", nickname))))
                .andExpect(status().isCreated());
        Long userId = userRepository.findByEmail(email).orElseThrow().getUserId();
        return jwtTokenProvider.createAccessToken(userId, Role.ROLE_USER);
    }

    private void register(String token, UUID companyId) throws Exception {
        mockMvc.perform(post("/api/users/me/watch-companies/{companyId}", companyId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token))
                .andExpect(status().isCreated());
    }

    // 상황: A, B, C 순서로 등록한 뒤 목록 조회
    // 기대: 200 + 최신순(C, B, A), 각 줄에 기업 정보와 등록 시각, 더 볼 것 없음
    @Test
    @DisplayName("관심 기업 목록은 등록 최신순으로 나온다")
    void list_latest_first() throws Exception {
        register(myToken, COMPANY_A);
        register(myToken, COMPANY_B);
        register(myToken, COMPANY_C);

        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(3))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_C.toString()))
                .andExpect(jsonPath("$.items[0].name").value("목록기업C"))
                .andExpect(jsonPath("$.items[0].stockCode").value("AAPL0"))
                .andExpect(jsonPath("$.items[0].market").value("NASDAQ"))
                .andExpect(jsonPath("$.items[0].watchedAt").isString())
                .andExpect(jsonPath("$.items[2].companyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.hasNext").value(false))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
    }

    // 상황: 대표 산업이 있는 기업(A: 반도체)과 없는 기업(B)을 등록하고 조회
    // 기대: A는 대표 산업 하나만(부수 산업 아님), B는 primaryIndustry가 null
    @Test
    @DisplayName("대표 산업이 있으면 함께, 없으면 null로 나온다")
    void list_primary_industry() throws Exception {
        register(myToken, COMPANY_A);
        register(myToken, COMPANY_B);

        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_B.toString()))
                .andExpect(jsonPath("$.items[0].primaryIndustry").value(org.hamcrest.Matchers.nullValue()))
                .andExpect(jsonPath("$.items[1].companyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.items[1].primaryIndustry.industryId").value(INDUSTRY_SEMI.toString()))
                .andExpect(jsonPath("$.items[1].primaryIndustry.name").value("목록테스트반도체"));
    }

    // 상황: 3개를 등록하고 size=2로 첫 페이지를 받은 뒤, 받은 커서로 다음 페이지 요청
    // 기대: 첫 페이지 2개(hasNext=true) → 다음 페이지 1개(hasNext=false), 중복 없이 이어짐
    @Test
    @DisplayName("커서로 다음 페이지를 이어서 볼 수 있다")
    void list_paging() throws Exception {
        register(myToken, COMPANY_A);
        register(myToken, COMPANY_B);
        register(myToken, COMPANY_C);

        MvcResult first = mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_C.toString()))
                .andExpect(jsonPath("$.items[1].companyId").value(COMPANY_B.toString()))
                .andExpect(jsonPath("$.hasNext").value(true))
                .andReturn();

        String cursor = objectMapper.readTree(first.getResponse().getContentAsString())
                .get("nextCursor").asString();

        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "2")
                        .param("cursor", cursor))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 내가 A를, 다른 사용자가 B를 등록한 뒤 내 목록 조회
    // 기대: 내 것(A)만 보인다
    @Test
    @DisplayName("다른 사용자의 관심 기업은 내 목록에 섞이지 않는다")
    void list_only_mine() throws Exception {
        register(myToken, COMPANY_A);
        String otherToken = signupAndToken("other-list@test.com", "다른목록사람");
        register(otherToken, COMPANY_B);

        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_A.toString()));
    }

    // 상황: 등록 후 기업이 비활성으로 바뀜
    // 기대: 서비스 대상이 아니므로 목록에서 빠진다
    @Test
    @DisplayName("비활성으로 바뀐 기업은 목록에서 빠진다")
    void list_excludes_inactive_company() throws Exception {
        register(myToken, COMPANY_A);
        register(myToken, COMPANY_INACTIVE);
        jdbcTemplate.update("UPDATE company SET status = 'INACTIVE' WHERE company_id = ?", COMPANY_INACTIVE);

        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_A.toString()));
    }

    // 상황: 아무것도 등록하지 않은 상태에서 조회
    // 기대: 200 + 빈 배열 (오류가 아니다)
    @Test
    @DisplayName("등록한 기업이 없으면 빈 목록을 돌려준다")
    void list_empty() throws Exception {
        mockMvc.perform(get("/api/users/me/watch-companies")
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
        mockMvc.perform(get("/api/users/me/watch-companies")
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
        mockMvc.perform(get("/api/users/me/watch-companies")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .param("size", "0"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 토큰 없이 목록 조회
    // 기대: 401 — 내 목록은 로그인 필요
    @Test
    @DisplayName("토큰 없이 관심 기업 목록을 볼 수 없다")
    void list_without_token() throws Exception {
        mockMvc.perform(get("/api/users/me/watch-companies"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }
}
