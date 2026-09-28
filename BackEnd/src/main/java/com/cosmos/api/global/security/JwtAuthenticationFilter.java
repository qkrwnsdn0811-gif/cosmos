package com.cosmos.api.global.security;

import com.cosmos.api.global.error.BusinessException;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpHeaders;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 요청 헤더의 Access Token을 확인해 로그인 상태로 만들어 주는 관문.
 *
 * <p>토큰이 아예 없으면 비로그인으로 통과시킨다(공개 API 때문). 다만 토큰이 있는데 문제가 있으면
 * 그 자리에서 401로 돌려보내, 프론트가 만료(TOKEN_EXPIRED)와 위조(TOKEN_INVALID)를 구분할 수 있게 한다.
 */
@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtTokenProvider jwtTokenProvider;
    private final AuthErrorResponder authErrorResponder;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String token = resolveToken(request);
        if (token == null) {
            chain.doFilter(request, response);
            return;
        }

        try {
            AuthUser authUser = jwtTokenProvider.parseAccessToken(token);
            SecurityContextHolder.getContext().setAuthentication(toAuthentication(authUser));
        } catch (BusinessException e) {
            SecurityContextHolder.clearContext();
            authErrorResponder.write(response, e.getErrorCode());
            return; // 뒤 단계로 넘기지 않고 여기서 응답을 끝낸다
        }

        chain.doFilter(request, response);
    }

    private String resolveToken(HttpServletRequest request) {
        String header = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (header == null || !header.startsWith(BEARER_PREFIX)) {
            return null;
        }
        String token = header.substring(BEARER_PREFIX.length()).trim();
        return token.isEmpty() ? null : token;
    }

    private UsernamePasswordAuthenticationToken toAuthentication(AuthUser authUser) {
        // Role 이름이 ROLE_ 로 시작하므로 hasRole("ADMIN") 같은 검사에 그대로 쓸 수 있다
        var authorities = List.of(new SimpleGrantedAuthority(authUser.role().name()));
        return new UsernamePasswordAuthenticationToken(authUser, null, authorities);
    }
}
