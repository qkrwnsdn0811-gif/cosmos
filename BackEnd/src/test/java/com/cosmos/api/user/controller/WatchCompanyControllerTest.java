package com.cosmos.api.user.controller;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.cosmos.api.global.security.JwtTokenProvider;
import com.cosmos.api.user.dto.SignupRequest;
import com.cosmos.api.user.entity.Role;
import com.cosmos.api.user.entity.UserWatchCompanyId;
import com.cosmos.api.user.repository.UserRepository;
import com.cosmos.api.user.repository.UserWatchCompanyRepository;
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
import org.springframework.test.web.servlet.ResultActions;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

/**
 * 관심 기업 등록·해제 API 테스트.
 * "내 목록에만 반영되는지", "중복·없는 기업 처리", "해제는 멱등인지"가 핵심이라 그 세 가지를 중심으로 확인한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@Transactional
class WatchCompanyControllerTest {

    private static final UUID COMPANY_ACTIVE = UUID.fromString("60000000-0000-0000-0000-00000000000a");
    private static final UUID COMPANY_INACTIVE = UUID.fromString("60000000-0000-0000-0000-00000000000b");
    private static final UUID UNKNOWN_COMPANY = UUID.fromString("60000000-0000-0000-0000-0000000000ff");

    @Autowired
    MockMvc mockMvc;

    @Autowired
    ObjectMapper objectMapper;

    @Autowired
    JdbcTemplate jdbcTemplate;

    @Autowired
    UserRepository userRepository;

    @Autowired
    UserWatchCompanyRepository watchRepository;

    @Autowired
    JwtTokenProvider jwtTokenProvider;

    private Long myUserId;
    private String myToken;

    /** 활성 기업 하나, 비활성 기업 하나, 회원 한 명을 준비한다. */
    @BeforeEach
    void setUp() throws Exception {
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, ?, 'ACTIVE')",
                COMPANY_ACTIVE, "관심테스트기업");
        jdbcTemplate.update("INSERT INTO company (company_id, name, status) VALUES (?, ?, 'INACTIVE')",
                COMPANY_INACTIVE, "상장폐지기업");

        myToken = signupAndToken("watch@test.com", "관심테스터");
        myUserId = userRepository.findByEmail("watch@test.com").orElseThrow().getUserId();
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

    private ResultActions register(String token, Object companyId) throws Exception {
        return mockMvc.perform(post("/api/users/me/watch-companies/{companyId}", companyId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token));
    }

    private ResultActions unregister(String token, Object companyId) throws Exception {
        return mockMvc.perform(delete("/api/users/me/watch-companies/{companyId}", companyId)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + token));
    }

    private boolean isWatched(Long userId, UUID companyId) {
        return watchRepository.existsById(new UserWatchCompanyId(userId, companyId));
    }

    // 상황: 로그인한 사용자가 활성 기업을 관심 기업으로 등록
    // 기대: 201 + 기업 번호·등록 시각, DB에 내 관심 기업으로 남는다
    @Test
    @DisplayName("관심 기업을 등록하면 201과 등록 정보를 돌려준다")
    void register_success() throws Exception {
        register(myToken, COMPANY_ACTIVE)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.companyId").value(COMPANY_ACTIVE.toString()))
                .andExpect(jsonPath("$.watchedAt").isString());

        assertThat(isWatched(myUserId, COMPANY_ACTIVE)).isTrue();
    }

    // 상황: 같은 기업을 두 번 등록
    // 기대: 두 번째는 409 WATCH_COMPANY_DUPLICATED
    @Test
    @DisplayName("이미 등록한 기업을 다시 등록하면 409")
    void register_duplicated() throws Exception {
        register(myToken, COMPANY_ACTIVE).andExpect(status().isCreated());

        register(myToken, COMPANY_ACTIVE)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("WATCH_COMPANY_DUPLICATED"));
    }

    // 상황: 존재하지 않는 기업 번호로 등록
    // 기대: 404 COMPANY_NOT_FOUND
    @Test
    @DisplayName("없는 기업은 등록할 수 없다")
    void register_company_not_found() throws Exception {
        register(myToken, UNKNOWN_COMPANY)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }

    // 상황: 있긴 하지만 서비스 대상이 아닌(비활성) 기업으로 등록
    // 기대: 없는 기업과 똑같이 404
    @Test
    @DisplayName("비활성 기업은 없는 기업처럼 404")
    void register_inactive_company() throws Exception {
        register(myToken, COMPANY_INACTIVE)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("COMPANY_NOT_FOUND"));
    }

    // 상황: 기업 번호 자리에 UUID가 아닌 문자열
    // 기대: 400 VALIDATION_FAILED
    @Test
    @DisplayName("기업 번호 형식이 잘못되면 400")
    void register_invalid_company_id() throws Exception {
        register(myToken, "not-a-uuid")
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("VALIDATION_FAILED"));
    }

    // 상황: 토큰 없이 등록 시도
    // 기대: 401 — 관심 기업은 로그인 필요
    @Test
    @DisplayName("토큰 없이 관심 기업을 등록할 수 없다")
    void register_without_token() throws Exception {
        mockMvc.perform(post("/api/users/me/watch-companies/{companyId}", COMPANY_ACTIVE))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("TOKEN_INVALID"));
    }

    // 상황: 등록한 기업을 해제
    // 기대: 204, DB에서 사라진다
    @Test
    @DisplayName("관심 기업을 해제하면 204와 함께 목록에서 빠진다")
    void unregister_success() throws Exception {
        register(myToken, COMPANY_ACTIVE).andExpect(status().isCreated());

        unregister(myToken, COMPANY_ACTIVE).andExpect(status().isNoContent());

        assertThat(isWatched(myUserId, COMPANY_ACTIVE)).isFalse();
    }

    // 상황: 등록한 적 없는 기업(또는 없는 기업 번호)을 해제
    // 기대: 오류 없이 204 (멱등 — 두 번 눌러도 결과가 같다)
    @Test
    @DisplayName("등록하지 않은 기업을 해제해도 204")
    void unregister_is_idempotent() throws Exception {
        unregister(myToken, COMPANY_ACTIVE).andExpect(status().isNoContent());
        unregister(myToken, UNKNOWN_COMPANY).andExpect(status().isNoContent());
    }

    // 상황: 내가 등록한 기업을 다른 사용자가 해제 시도
    // 기대: 그 사용자 목록에서만 빠질 뿐(원래 없음), 내 등록은 그대로 남는다
    @Test
    @DisplayName("다른 사용자의 관심 기업에는 영향을 주지 않는다")
    void unregister_does_not_touch_other_users() throws Exception {
        register(myToken, COMPANY_ACTIVE).andExpect(status().isCreated());
        String otherToken = signupAndToken("other@test.com", "다른사람");

        unregister(otherToken, COMPANY_ACTIVE).andExpect(status().isNoContent());

        assertThat(isWatched(myUserId, COMPANY_ACTIVE)).isTrue();
    }
}
