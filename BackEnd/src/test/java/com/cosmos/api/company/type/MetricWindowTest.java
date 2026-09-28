package com.cosmos.api.company.type;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.global.error.BusinessException;
import java.time.LocalDate;
import org.junit.jupiter.api.Test;

class MetricWindowTest {

    @Test
    void supportsNewServiceWindowsCaseInsensitively() {
        assertThat(MetricWindow.from("30d")).isEqualTo(MetricWindow.DAYS_30);
        assertThat(MetricWindow.from("1y")).isEqualTo(MetricWindow.YEAR_1);
        assertThat(MetricWindow.from("10Y")).isEqualTo(MetricWindow.YEARS_10);
    }

    @Test
    void rejectsLegacyServiceWindows() {
        assertThatThrownBy(() -> MetricWindow.from("7D")).isInstanceOf(BusinessException.class);
        assertThatThrownBy(() -> MetricWindow.from("90D")).isInstanceOf(BusinessException.class);
    }

    @Test
    void calculatesInclusiveCalendarRanges() {
        LocalDate leapDay = LocalDate.of(2028, 2, 29);

        assertThat(MetricWindow.DAYS_30.inclusiveStartDate(leapDay))
                .isEqualTo(LocalDate.of(2028, 1, 31));
        assertThat(MetricWindow.YEAR_1.inclusiveStartDate(leapDay))
                .isEqualTo(LocalDate.of(2027, 3, 1));
        assertThat(MetricWindow.YEARS_10.inclusiveStartDate(leapDay))
                .isEqualTo(LocalDate.of(2018, 3, 1));
    }
}
