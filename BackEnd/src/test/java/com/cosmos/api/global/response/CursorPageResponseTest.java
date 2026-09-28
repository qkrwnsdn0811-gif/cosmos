package com.cosmos.api.global.response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class CursorPageResponseTest {

    @Test
    void 커서_기반_목록_응답을_생성한다() {
        CursorPageResponse<String> response = CursorPageResponse.of(
                List.of("item-1", "item-2"),
                "next-cursor",
                true);

        assertThat(response.items()).containsExactly("item-1", "item-2");
        assertThat(response.nextCursor()).isEqualTo("next-cursor");
        assertThat(response.hasNext()).isTrue();
    }

    @Test
    void 항목_목록은_응답_생성_후_변경되지_않는다() {
        List<String> source = new ArrayList<>(List.of("item-1"));

        CursorPageResponse<String> response = CursorPageResponse.of(source, null, false);
        source.clear();

        assertThat(response.items()).containsExactly("item-1");
        assertThatThrownBy(() -> response.items().clear())
                .isInstanceOf(UnsupportedOperationException.class);
    }
}
