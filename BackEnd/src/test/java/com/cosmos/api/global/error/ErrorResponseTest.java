package com.cosmos.api.global.error;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class ErrorResponseTest {

    @Test
    void 공통_오류_응답을_생성한다() {
        ErrorResponse response = ErrorResponse.from(CommonErrorCode.INVALID_CURSOR);

        assertThat(response.code()).isEqualTo("INVALID_CURSOR");
        assertThat(response.message()).isEqualTo("커서가 올바르지 않습니다.");
        assertThat(response.fieldErrors()).isEmpty();
    }

    @Test
    void 필드_오류_목록은_응답_생성_후_변경되지_않는다() {
        List<FieldErrorResponse> source = new ArrayList<>();
        source.add(new FieldErrorResponse("email", "이메일 형식이어야 합니다."));

        ErrorResponse response = ErrorResponse.validation(source);
        source.clear();

        assertThat(response.fieldErrors()).hasSize(1);
        assertThatThrownBy(() -> response.fieldErrors().clear())
                .isInstanceOf(UnsupportedOperationException.class);
    }
}
