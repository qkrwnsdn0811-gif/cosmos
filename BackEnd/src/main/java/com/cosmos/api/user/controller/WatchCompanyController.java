package com.cosmos.api.user.controller;

import com.cosmos.api.global.response.CursorPageResponse;
import com.cosmos.api.global.security.AuthUser;
import com.cosmos.api.user.dto.WatchCompanyItemResponse;
import com.cosmos.api.user.dto.WatchCompanyResponse;
import com.cosmos.api.user.service.WatchCompanyService;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** 내 관심 기업 API. 모두 Access Token이 필요하다. */
@RestController
@RequestMapping("/api/users/me/watch-companies")
@RequiredArgsConstructor
@Validated // 쿼리 파라미터(size) 검증 활성화
public class WatchCompanyController {

    private final WatchCompanyService watchCompanyService;

    /** 내 관심 기업 목록 (등록 최신순). */
    @GetMapping
    public CursorPageResponse<WatchCompanyItemResponse> findMine(
            @AuthenticationPrincipal AuthUser authUser,
            @RequestParam(required = false) String cursor,
            @RequestParam(defaultValue = "20") @Min(1) @Max(100) int size
    ) {
        return watchCompanyService.findMyWatchCompanies(authUser.userId(), cursor, size);
    }

    /** 관심 기업 등록. 바디 없이 기업 번호만 주소에 담는다. */
    @PostMapping("/{companyId}")
    public ResponseEntity<WatchCompanyResponse> register(
            @AuthenticationPrincipal AuthUser authUser,
            @PathVariable UUID companyId
    ) {
        WatchCompanyResponse response = watchCompanyService.register(authUser.userId(), companyId);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /** 관심 기업 해제. 이미 해제된 상태여도 204(멱등). */
    @DeleteMapping("/{companyId}")
    public ResponseEntity<Void> unregister(
            @AuthenticationPrincipal AuthUser authUser,
            @PathVariable UUID companyId
    ) {
        watchCompanyService.unregister(authUser.userId(), companyId);
        return ResponseEntity.noContent().build();
    }
}
