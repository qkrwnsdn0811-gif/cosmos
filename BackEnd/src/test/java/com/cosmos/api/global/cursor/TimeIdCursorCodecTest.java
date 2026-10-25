package com.cosmos.api.global.cursor;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.nio.ByteBuffer;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class TimeIdCursorCodecTest {

    private final TimeIdCursorCodec codec = new TimeIdCursorCodec();

    private static String crafted(long epochSecond) {
        ByteBuffer buffer = ByteBuffer.allocate(Long.BYTES * 4);
        buffer.putLong(epochSecond).putLong(0).putLong(1).putLong(2);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(buffer.array());
    }

    @Test
    void decodesItsOwnEncoding() {
        TimeIdCursor cursor = new TimeIdCursor(Instant.parse("2026-10-13T01:00:00Z"), UUID.randomUUID());
        assertThat(codec.decode(codec.encode(cursor))).isEqualTo(cursor);
    }

    @Test
    void epochSecondOutsideInstantRangeIsInvalidCursor() {
        // 32바이트 길이는 맞지만 Instant로 만들 수 없는 시각 → 500이 아니라 400 INVALID_CURSOR
        assertThatThrownBy(() -> codec.decode(crafted(Long.MAX_VALUE)))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.INVALID_CURSOR);
    }

    @Test
    void epochSecondBeyondDatabaseRangeIsInvalidCursor() {
        // Instant로는 만들어지지만 PostgreSQL TIMESTAMPTZ 범위를 넘어 쿼리에서 500이 나던 값
        assertThatThrownBy(() -> codec.decode(crafted(10_000_000_000_000L)))
                .isInstanceOf(BusinessException.class)
                .extracting(e -> ((BusinessException) e).getErrorCode()).isEqualTo(CommonErrorCode.INVALID_CURSOR);
    }
}
