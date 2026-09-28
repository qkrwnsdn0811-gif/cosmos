package com.cosmos.api;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Clock;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

@SpringBootTest
class CosmosApplicationTests {

	@Autowired
	private Clock clock;

	@Autowired
	private JdbcTemplate jdbcTemplate;

	@Test
	void contextLoads() {
	}

	@Test
	void 애플리케이션과_DB_세션은_UTC를_사용한다() {
		String databaseTimeZone = jdbcTemplate.queryForObject("SHOW TIME ZONE", String.class);

		assertThat(clock.getZone()).isEqualTo(ZoneOffset.UTC);
		assertThat(databaseTimeZone).isEqualTo("UTC");
	}

}
