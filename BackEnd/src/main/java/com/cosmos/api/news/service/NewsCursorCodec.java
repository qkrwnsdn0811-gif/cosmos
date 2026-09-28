package com.cosmos.api.news.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.nio.ByteBuffer;
import java.time.DateTimeException;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class NewsCursorCodec {

    private static final int CURSOR_BYTES = Long.BYTES * 4;

    String encode(NewsCursor cursor) {
        ByteBuffer buffer = ByteBuffer.allocate(CURSOR_BYTES);
        buffer.putLong(cursor.publishedAt().getEpochSecond());
        buffer.putLong(cursor.publishedAt().getNano());
        buffer.putLong(cursor.newsId().getMostSignificantBits());
        buffer.putLong(cursor.newsId().getLeastSignificantBits());
        return Base64.getUrlEncoder().withoutPadding().encodeToString(buffer.array());
    }

    NewsCursor decode(String encodedCursor) {
        if (encodedCursor == null) {
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
            if (nano < 0 || nano > 999_999_999) {
                throw invalidCursor();
            }
            return new NewsCursor(
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
