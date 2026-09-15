package com.cosmos.api.community.controller;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.community.dto.CommentRequest;
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
 * 댓글 목록 조회 API 테스트 (기업별 / 전체).
 * 최신순 정렬, 커서로 이어보기, 비로그인 접근이 핵심이라 그 세 가지를 중심으로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class CommentListControllerTest {

    private static final UUID COMPANY_A = UUID.fromString("50000000-0000-0000-0000-00000000000a");
    private static final UUID COMPANY_B = UUID.fromString("50000000-0000-0000-0000-00000000000b");
    private static final UUID UNKNOWN_COMPANY = UUID.fromString("50000000-0000-0000-0000-0000000000ff");

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

    private String token;

    /** 기업 두 곳과 회원 한 명을 준비하고, 기존 댓글이 섞이지 않도록 테이블을 비운다. */
    @BeforeEach
    void setUp() throws Exception {
        jdbcTemplate.update("DELETE FROM company_community_comment");
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, ?, 'ACTIVE')",
                COMPANY_A, "목록테스트기업A");
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, ?, 'ACTIVE')",
                COMPANY_B, "목록테스트기업B");

        mockMvc.perform(post("/api/auth/signup")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(
                                new SignupRequest("list@test.com", "password1", "목록테스터"))))
                .andExpect(status().isCreated());
        Long userId = userRepository.findByEmail("list@test.com").orElseThrow().getUserId();
        token = jwtTokenProvider.createAccessToken(userId, Role.ROLE_USER);
    }

    private void writeComment(UUID companyId, String content) throws Exception {
        mockMvc.perform(post("/api/companies/{companyId}/comments", companyId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + token)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CommentRequest(content))))
                .andExpect(status().isCreated());
    }

    // 상황: 기업 A에 댓글 3개를 순서대로 작성한 뒤 목록 조회 (토큰 없이)
    // 기대: 200 + 최신순(마지막에 쓴 것이 맨 위), 작성자 정보 포함, 더 볼 것 없음
    @Test
    @DisplayName("기업별 댓글 목록은 최신순으로 나오고 비로그인도 볼 수 있다")
    void company_comments_latest_first() throws Exception {
        writeComment(COMPANY_A, "첫번째");
        writeComment(COMPANY_A, "두번째");
        writeComment(COMPANY_A, "세번째");

        mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_A))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(3))
                .andExpect(jsonPath("$.items[0].content").value("세번째"))
                .andExpect(jsonPath("$.items[2].content").value("첫번째"))
                .andExpect(jsonPath("$.items[0].author.nickname").value("목록테스터"))
                .andExpect(jsonPath("$.items[0].companyId").value(COMPANY_A.toString()))
                .andExpect(jsonPath("$.items[0].edited").value(false))
                .andExpect(jsonPath("$.hasNext").value(false))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
    }

    // 상황: 기업 A에만 댓글을 쓰고 기업 B 목록을 조회
    // 기대: 다른 기업 댓글은 섞이지 않고 빈 목록
    @Test
    @DisplayName("다른 기업의 댓글은 섞이지 않는다")
    void company_comments_are_isolated() throws Exception {
        writeComment(COMPANY_A, "A기업 댓글");

        mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_B))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(0))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 댓글 3개를 쓰고 size=2로 첫 페이지를 받은 뒤, 받은 커서로 다음 페이지 요청
    // 기대: 첫 페이지 2개(hasNext=true) → 다음 페이지 1개(hasNext=false), 중복 없이 이어짐
    @Test
    @DisplayName("커서로 다음 페이지를 이어서 볼 수 있다")
    void company_comments_paging() throws Exception {
        writeComment(COMPANY_A, "첫번째");
        writeComment(COMPANY_A, "두번째");
        writeComment(COMPANY_A, "세번째");

        MvcResult first = mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_A)
                        .param("size", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].content").value("세번째"))
                .andExpect(jsonPath("$.items[1].content").value("두번째"))
                .andExpect(jsonPath("$.hasNext").value(true))
                .andReturn();

        String cursor = objectMapper.readTree(first.getResponse().getContentAsString())
                .get("nextCursor").asString();

        mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_A)
                        .param("size", "2")
                        .param("cursor", cursor))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].content").value("첫번째"))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 커서 자리에 아무 문자열을 넣어 요청
    // 기대: 400 INVALID_CURSOR
    @Test
    @DisplayName("커서 형식이 잘못되면 400 INVALID_CURSOR")
    void invalid_cursor() throws Exception {
        mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_A)
                        .param("cursor", "broken-cursor"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_CURSOR"));
    }

    // 상황: size에 허용 범위(1~100)를 벗어난 값을 넣어 요청
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("size가 허용 범위를 벗어나면 400")
    void invalid_size() throws Exception {
        mockMvc.perform(get("/api/companies/{companyId}/comments", COMPANY_A)
                        .param("size", "101"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 없는 기업 번호로 목록 조회
    // 기대: 404 COMPANY_NOT_FOUND (빈 목록이 아니라 오류로 알려준다)
    @Test
    @DisplayName("없는 기업의 댓글 목록은 404")
    void company_not_found() throws Exception {
        mockMvc.perform(get("/api/companies/{companyId}/comments", UNKNOWN_COMPANY))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }

    // 상황: 서로 다른 기업에 댓글을 쓰고 전체 목록 조회 (토큰 없이)
    // 기대: 기업 구분 없이 최신순으로 합쳐지고, 각 줄에 기업 이름이 담긴다
    @Test
    @DisplayName("전체 커뮤니티 목록은 여러 기업 댓글을 최신순으로 합쳐 보여준다")
    void all_comments_merged() throws Exception {
        writeComment(COMPANY_A, "A기업 댓글");
        writeComment(COMPANY_B, "B기업 댓글");

        mockMvc.perform(get("/api/community/comments"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].content").value("B기업 댓글"))
                .andExpect(jsonPath("$.items[0].company.name").value("목록테스트기업B"))
                .andExpect(jsonPath("$.items[0].company.companyId").value(COMPANY_B.toString()))
                .andExpect(jsonPath("$.items[0].author.nickname").value("목록테스터"))
                .andExpect(jsonPath("$.items[1].content").value("A기업 댓글"))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 전체 목록을 size=1로 나눠 받기
    // 기대: 커서로 다음 기업의 댓글까지 이어서 조회된다
    @Test
    @DisplayName("전체 커뮤니티 목록도 커서로 이어볼 수 있다")
    void all_comments_paging() throws Exception {
        writeComment(COMPANY_A, "A기업 댓글");
        writeComment(COMPANY_B, "B기업 댓글");

        MvcResult first = mockMvc.perform(get("/api/community/comments").param("size", "1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].content").value("B기업 댓글"))
                .andExpect(jsonPath("$.hasNext").value(true))
                .andReturn();

        String cursor = objectMapper.readTree(first.getResponse().getContentAsString())
                .get("nextCursor").asString();

        mockMvc.perform(get("/api/community/comments").param("size", "1").param("cursor", cursor))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].content").value("A기업 댓글"))
                .andExpect(jsonPath("$.hasNext").value(false));
    }

    // 상황: 댓글이 하나도 없는 상태에서 전체 목록 조회
    // 기대: 200 + 빈 배열 (오류가 아니다)
    @Test
    @DisplayName("댓글이 없으면 빈 목록을 돌려준다")
    void empty_list() throws Exception {
        mockMvc.perform(get("/api/community/comments"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(0))
                .andExpect(jsonPath("$.hasNext").value(false));
    }
}
