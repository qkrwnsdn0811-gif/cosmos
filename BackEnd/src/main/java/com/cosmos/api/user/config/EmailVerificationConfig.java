package com.cosmos.api.user.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** app.email-verification 설정을 EmailVerificationProperties 로 읽어들인다. */
@Configuration
@EnableConfigurationProperties(EmailVerificationProperties.class)
public class EmailVerificationConfig {
}
