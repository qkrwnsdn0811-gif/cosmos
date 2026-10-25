package com.cosmos.api.global.cursor;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.nio.ByteBuffer;
import java.time.DateTimeException;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import org.springframework.stereotype.Component;

/** 커서를 짧은 문자열로 바꾸고 되돌린다. 형식이 깨졌으면 400 INVALID_CURSOR. */
@Component
public class TimeIdCursorCodec {

    private static final int CURSOR_BYTES = Long.BYTES * 4;
    /** 0001-01-01T00:00:00Z ~ 9999-12-31T23:59:59Z. 이 밖의 시각은 DB(TIMESTAMPTZ)에서 오류가 나므로 커서 단계에서 막는다. */
    private static final long MIN_EPOCH_SECOND = -62_135_596_800L;
    private static final long MAX_EPOCH_SECOND = 253_402_300_799L;

    public String encode(TimeIdCursor cursor) {
        ByteBuffer buffer = ByteBuffer.allocate(CURSOR_BYTES);
        buffer.putLong(cursor.at().getEpochSecond());
        buffer.putLong(cursor.at().getNano());
        buffer.putLong(cursor.id().getMostSignificantBits());
        buffer.putLong(cursor.id().getLeastSignificantBits());
        return Base64.getUrlEncoder().withoutPadding().encodeToString(buffer.array());
    }

    /** 커서가 없으면(첫 페이지) null을 돌려준다. */
    public TimeIdCursor decode(String encodedCursor) {
        if (encodedCursor == null || encodedCursor.isBlank()) {
            return null;
        }
        try {
            byte[] bytes = Base64.getUrlDecoder().decode(encodedCursor);
            if (bytes.length != CURSOR_BYTES) {
                throw invalidCursor();
            }
            ByteBuffer buffer = ByteBuffer.wrap(bytes);
            long epochSecond = buffer.getLong();
            long nano = buffer.getLong();
            if (nano < 0 || nano > 999_999_999 || epochSecond < MIN_EPOCH_SECOND || epochSecond > MAX_EPOCH_SECOND) {
                throw invalidCursor();
            }
            return new TimeIdCursor(
                    Instant.ofEpochSecond(epochSecond, nano),
                    new UUID(buffer.getLong(), buffer.getLong()));
        } catch (IllegalArgumentException | DateTimeException exception) {
            throw invalidCursor();
        }
    }

    private BusinessException invalidCursor() {
        return new BusinessException(CommonErrorCode.INVALID_CURSOR);
    }
}
