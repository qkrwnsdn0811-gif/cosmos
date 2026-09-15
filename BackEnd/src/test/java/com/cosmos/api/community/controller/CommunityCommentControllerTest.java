package com.cosmos.api.community.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.community.dto.CommentRequest;
import com.cosmos.api.community.repository.CommunityCommentRepository;
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
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 커뮤니티 댓글 작성·수정·삭제 API 테스트.
 * 핵심은 "본인 댓글만 수정·삭제할 수 있는가"이므로, 남의 댓글을 건드리는 경우를 함께 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class CommunityCommentControllerTest {

    private static final UUID COMPANY_ID = UUID.fromString("40000000-0000-0000-0000-000000000001");
    private static final UUID INACTIVE_COMPANY_ID = UUID.fromString("40000000-0000-0000-0000-000000000002");
    private static final UUID UNKNOWN_ID = UUID.fromString("40000000-0000-0000-0000-0000000000ff");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    UserRepository userRepository;

    @Autowired
    CommunityCommentRepository commentRepository;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    private String myToken;
    private String otherToken;

    /** 댓글을 달 기업 하나와, 서로 다른 회원 두 명(나 / 남)을 준비한다. */
    @BeforeEach
    void setUp() throws Exception {
        insertCompany(COMPANY_ID, "댓글테스트기업", "ACTIVE");
        insertCompany(INACTIVE_COMPANY_ID, "폐지된기업", "INACTIVE");

        myToken = signupAndToken("writer@test.com", "댓글작성자");
        otherToken = signupAndToken("other@test.com", "다른사람");
    }

    private void insertCompany(UUID id, String name, String status) {
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, ?, ?)", id, name, status);
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

    private ResultActions writeComment(String token, UUID companyId, String content) throws Exception {
        return mockMvc.perform(post("/api/companies/{companyId}/comments", companyId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new CommentRequest(content))));
    }

    /** 댓글 하나를 작성하고 그 번호를 돌려준다. */
    private UUID writeAndGetId(String token) throws Exception {
        MvcResult result = writeComment(token, COMPANY_ID, "처음 쓴 댓글")
                .andExpect(status().isCreated())
                .andReturn();
        return UUID.fromString(objectMapper.readTree(result.getResponse().getContentAsString())
                .get("commentId").asString());
    }

    // 상황: 로그인한 사용자가 기업 커뮤니티에 댓글 작성
    // 기대: 201 + 댓글 정보, 아직 수정 전이므로 edited=false
    @Test
    @DisplayName("댓글을 작성하면 201과 작성된 댓글 정보를 돌려준다")
    void create_comment() throws Exception {
        writeComment(myToken, COMPANY_ID, "최근 공급망 뉴스가 인상적이네요.")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.commentId").isString())
                .andExpect(jsonPath("$.companyId").value(COMPANY_ID.toString()))
                .andExpect(jsonPath("$.content").value("최근 공급망 뉴스가 인상적이네요."))
                .andExpect(jsonPath("$.edited").value(false));
    }

    // 상황: 앞뒤에 공백이 붙은 내용으로 작성
    // 기대: 공백을 떼어내고 저장한다
    @Test
    @DisplayName("댓글 앞뒤 공백은 제거하고 저장한다")
    void create_comment_trims_content() throws Exception {
        writeComment(myToken, COMPANY_ID, "   공백 붙은 댓글   ")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.content").value("공백 붙은 댓글"));
    }

    // 상황: 토큰 없이 댓글 작성 시도
    // 기대: 401 — 댓글 작성은 로그인 필요
    @Test
    @DisplayName("토큰 없이 댓글을 쓸 수 없다")
    void create_comment_without_token() throws Exception {
        mockMvc.perform(post("/api/companies/{companyId}/comments", COMPANY_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CommentRequest("비회원 댓글"))))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 없는 기업 번호로 댓글 작성
    // 기대: 404 COMPANY_NOT_FOUND
    @Test
    @DisplayName("없는 기업에는 댓글을 쓸 수 없다")
    void create_comment_unknown_company() throws Exception {
        writeComment(myToken, UNKNOWN_ID, "없는 기업 댓글")
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }

    // 상황: 서비스 중지(INACTIVE) 상태 기업에 댓글 작성
    // 기대: 404 COMPANY_NOT_FOUND — 조회 API와 같은 기준을 따른다
    @Test
    @DisplayName("서비스 중지된 기업에는 댓글을 쓸 수 없다")
    void create_comment_inactive_company() throws Exception {
        writeComment(myToken, INACTIVE_COMPANY_ID, "중지된 기업 댓글")
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }

    // 상황: 내용이 비어 있거나 공백만 있는 댓글
    // 기대: 400 + content 필드 오류
    @Test
    @DisplayName("내용이 공백뿐이면 400")
    void create_comment_blank() throws Exception {
        writeComment(myToken, COMPANY_ID, "   ")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"))
                .andExpect(jsonPath("$.fieldErrors[0].field").value("content"));
    }

    // 상황: 500자를 넘는 댓글
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("500자를 넘으면 400")
    void create_comment_too_long() throws Exception {
        writeComment(myToken, COMPANY_ID, "가".repeat(501))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 내가 쓴 댓글 수정
    // 기대: 200 + 바뀐 내용, edited=true
    @Test
    @DisplayName("내 댓글을 수정하면 내용이 바뀌고 수정됨 표시가 켜진다")
    void update_own_comment() throws Exception {
        UUID commentId = writeAndGetId(myToken);

        mockMvc.perform(patch("/api/comments/{commentId}", commentId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CommentRequest("수정한 댓글 내용입니다."))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.commentId").value(commentId.toString()))
                .andExpect(jsonPath("$.content").value("수정한 댓글 내용입니다."))
                .andExpect(jsonPath("$.edited").value(true));
    }

    // 상황: 남이 쓴 댓글을 수정 시도
    // 기대: 403 COMMENT_FORBIDDEN, 내용은 그대로
    @Test
    @DisplayName("남의 댓글은 수정할 수 없다")
    void update_other_comment() throws Exception {
        UUID commentId = writeAndGetId(myToken);

        mockMvc.perform(patch("/api/comments/{commentId}", commentId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CommentRequest("남의 댓글 고치기"))))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("COMMENT_FORBIDDEN"));

        assertThat(commentRepository.findById(commentId).orElseThrow().getContent())
                .isEqualTo("처음 쓴 댓글");
    }

    // 상황: 없는 댓글 번호로 수정 시도
    // 기대: 404 COMMENT_NOT_FOUND
    @Test
    @DisplayName("없는 댓글을 수정하면 404")
    void update_unknown_comment() throws Exception {
        mockMvc.perform(patch("/api/comments/{commentId}", UNKNOWN_ID)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(objectMapper.writeValueAsString(new CommentRequest("없는 댓글 수정"))))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMMENT_NOT_FOUND"));
    }

    // 상황: 내가 쓴 댓글 삭제
    // 기대: 204 + DB에서 실제로 사라짐
    @Test
    @DisplayName("내 댓글을 삭제하면 204이고 실제로 지워진다")
    void delete_own_comment() throws Exception {
        UUID commentId = writeAndGetId(myToken);

        mockMvc.perform(delete("/api/comments/{commentId}", commentId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isNoContent());

        assertThat(commentRepository.findById(commentId)).isEmpty();
    }

    // 상황: 남이 쓴 댓글을 삭제 시도
    // 기대: 403 COMMENT_FORBIDDEN, 댓글은 그대로 남음
    @Test
    @DisplayName("남의 댓글은 삭제할 수 없다")
    void delete_other_comment() throws Exception {
        UUID commentId = writeAndGetId(myToken);

        mockMvc.perform(delete("/api/comments/{commentId}", commentId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + otherToken))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("COMMENT_FORBIDDEN"));

        assertThat(commentRepository.findById(commentId)).isPresent();
    }

    // 상황: 댓글 번호 자리에 UUID가 아닌 값을 넣어 호출
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("댓글 번호 형식이 잘못되면 400")
    void delete_invalid_id_format() throws Exception {
        mockMvc.perform(delete("/api/comments/{commentId}", "not-a-uuid")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + myToken))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }
}
