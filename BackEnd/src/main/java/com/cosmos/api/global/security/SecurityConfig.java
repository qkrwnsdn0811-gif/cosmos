package com.cosmos.api.global.security;

import java.util.List;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

/**
 * 어떤 API를 로그인 없이 열어줄지 정하고, JWT 관문을 요청 처리 앞단에 끼워 넣는다.
 *
 * <p>세션을 쓰지 않는 토큰 방식이라 서버는 로그인 상태를 저장하지 않는다(STATELESS).
 */
@Configuration
@EnableWebSecurity
@EnableConfigurationProperties({JwtProperties.class, AuthCookieProperties.class, CorsProperties.class})
@RequiredArgsConstructor
public class SecurityConfig {

    /** 비로그인 사용자도 볼 수 있는 조회 API (API 명세 "공개 조회 API" 기준). */
    private static final String[] PUBLIC_GET_PATHS = {
            "/api/companies/**",
            "/api/industries/**",
            "/api/news/**",
            "/api/graphs/**",
            "/api/relationships/**",
            "/api/community/**"
    };

    private final JwtAuthenticationFilter jwtAuthenticationFilter;
    private final AuthErrorResponder authErrorResponder;
    private final CorsProperties corsProperties;

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                // 토큰 기반이라 세션 로그인 관련 기본 기능은 모두 끈다
                .csrf(csrf -> csrf.disable())
                .httpBasic(basic -> basic.disable())
                .formLogin(form -> form.disable())
                .logout(logout -> logout.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))

                // 프론트(다른 주소)에서 쿠키를 포함해 호출할 수 있게 허용
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))

                .authorizeHttpRequests(auth -> auth
                        // 로그아웃은 "누구를 로그아웃시킬지" 알아야 하므로 로그인 상태에서만 호출한다
                        .requestMatchers(HttpMethod.POST, "/api/auth/logout").authenticated()
                        // 회원가입·로그인·중복확인 등은 로그인 전에 호출하므로 열어둔다
                        .requestMatchers("/api/auth/**").permitAll()
                        .requestMatchers("/actuator/health", "/actuator/info").permitAll()
                        .requestMatchers(HttpMethod.GET, PUBLIC_GET_PATHS).permitAll()
                        // 나머지(내 정보, 관심 기업, 스크랩, 댓글 작성 등)는 로그인 필요
                        .anyRequest().authenticated())

                // 인증·권한 실패도 우리 공통 오류 형식(code/message)으로 응답한다
                .exceptionHandling(handler -> handler
                        .authenticationEntryPoint((request, response, e) ->
                                authErrorResponder.write(response, AuthErrorCode.TOKEN_INVALID))
                        .accessDeniedHandler((request, response, e) ->
                                authErrorResponder.write(response, AuthErrorCode.FORBIDDEN)))

                .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    /**
     * CORS 규칙. 쿠키(Refresh Token)를 주고받으려면 allowCredentials가 켜져야 하고,
     * 그 경우 허용 주소에 "*"를 쓸 수 없어 프론트 주소를 정확히 적는다.
     */
    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(corsProperties.allowedOrigins());
        config.setAllowedMethods(List.of("GET", "POST", "PATCH", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of(HttpHeaders.AUTHORIZATION, HttpHeaders.CONTENT_TYPE));
        config.setAllowCredentials(true);
        config.setMaxAge(3600L); // 사전 확인(preflight) 결과를 1시간 기억해 요청 수를 줄인다

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/api/**", config);
        return source;
    }
}
