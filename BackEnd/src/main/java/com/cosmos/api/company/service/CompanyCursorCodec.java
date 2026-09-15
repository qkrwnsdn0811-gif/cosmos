package com.cosmos.api.company.service;

import com.cosmos.api.global.error.BusinessException;
import com.cosmos.api.global.error.CommonErrorCode;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Base64;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class CompanyCursorCodec {

    public String encode(CompanyCursor cursor) {
        try {
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (DataOutputStream output = new DataOutputStream(bytes)) {
                output.writeInt(cursor.searchRank());
                output.writeUTF(cursor.companyName());
                output.writeLong(cursor.companyId().getMostSignificantBits());
                output.writeLong(cursor.companyId().getLeastSignificantBits());
            }
            return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes.toByteArray());
        } catch (IOException exception) {
            throw new IllegalStateException("Failed to encode company cursor", exception);
        }
    }

    public CompanyCursor decode(String encodedCursor) {
        if (encodedCursor == null) {
            return null;
        }

        try {
            byte[] bytes = Base64.getUrlDecoder().decode(encodedCursor);
            try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(bytes))) {
                CompanyCursor cursor = new CompanyCursor(
                        input.readInt(),
                        input.readUTF(),
                        new UUID(input.readLong(), input.readLong()));
                if (input.available() != 0 || cursor.searchRank() < 0 || cursor.companyName().isBlank()) {
                    throw invalidCursor();
                }
                return cursor;
            }
        } catch (IllegalArgumentException | IOException exception) {
            throw invalidCursor();
        }
    }

    private BusinessException invalidCursor() {
        return new BusinessException(CommonErrorCode.INVALID_CURSOR);
    }
}
