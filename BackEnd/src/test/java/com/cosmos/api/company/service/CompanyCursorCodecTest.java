package com.cosmos.api.company.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class CompanyCursorCodecTest {

    private final CompanyCursorCodec codec = new CompanyCursorCodec();

    @Test
    void encodesAndDecodesCursor() {
        CompanyCursor cursor = new CompanyCursor(
                2,
                "삼성전자",
                UUID.fromString("00000000-0000-0000-0000-000000000001"));

        CompanyCursor decoded = codec.decode(codec.encode(cursor));

        assertThat(decoded).isEqualTo(cursor);
    }

    @Test
    void rejectsMalformedCursor() {
        assertThatThrownBy(() -> codec.decode("not-a-valid-cursor"))
                .isInstanceOf(BusinessException.class)
                .satisfies(exception -> assertThat(((BusinessException) exception).getErrorCode())
                        .isEqualTo(CommonErrorCode.INVALID_CURSOR));
    }
}
