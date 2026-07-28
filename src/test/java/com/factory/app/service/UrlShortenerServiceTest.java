package com.factory.app.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.factory.app.repository.ClickEventRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
class UrlShortenerServiceTest {

    @Autowired
    private UrlShortenerService service;

    @Autowired
    private ClickEventRepository clickEventRepository;

    @Test
    void shortenCreatesNewMappingWithCreatedTrue() {
        var result = service.shorten("https://example.com/service-test-" + System.nanoTime());
        assertThat(result.created()).isTrue();
        assertThat(result.code()).hasSize(7);
    }

    @Test
    void shortenTwiceDedupsAndReturnsSameCode() {
        String url = "https://example.com/dedup-test-" + System.nanoTime();
        var first = service.shorten(url);
        var second = service.shorten(url);

        assertThat(second.created()).isFalse();
        assertThat(second.code()).isEqualTo(first.code());
    }

    @Test
    void rejectsMissingUrl() {
        assertThatThrownBy(() -> service.shorten(null)).isInstanceOf(InvalidUrlException.class);
    }

    @Test
    void rejectsNonStringUrl() {
        assertThatThrownBy(() -> service.shorten(42)).isInstanceOf(InvalidUrlException.class);
    }

    @Test
    void rejectsMalformedUrl() {
        assertThatThrownBy(() -> service.shorten("not a url at all")).isInstanceOf(InvalidUrlException.class);
    }

    @Test
    void rejectsNonHttpScheme() {
        assertThatThrownBy(() -> service.shorten("ftp://example.com/file"))
                .isInstanceOf(InvalidUrlException.class);
        assertThatThrownBy(() -> service.shorten("javascript:alert(1)"))
                .isInstanceOf(InvalidUrlException.class);
        assertThatThrownBy(() -> service.shorten("data:text/html;base64,PHNjcmlwdD4="))
                .isInstanceOf(InvalidUrlException.class);
    }

    @Test
    void resolveAndRecordClickIncrementsCount() {
        String url = "https://example.com/click-test-" + System.nanoTime();
        var created = service.shorten(url);

        String resolved = service.resolveAndRecordClick(created.code());
        assertThat(resolved).isEqualTo(url);

        var stats = service.stats(created.code());
        assertThat(stats.clicks()).isEqualTo(1L);

        service.resolveAndRecordClick(created.code());
        assertThat(service.stats(created.code()).clicks()).isEqualTo(2L);
    }

    @Test
    @Transactional
    void resolveAndRecordClickPersistsClickEventInSameTransaction() {
        String url = "https://example.com/click-event-test-" + System.nanoTime();
        var created = service.shorten(url);

        String resolved = service.resolveAndRecordClick(created.code());
        assertThat(resolved).isEqualTo(url);

        // Same transaction: both the click_count increment and the new
        // ClickEvent row must already be visible without a commit.
        assertThat(service.stats(created.code()).clicks()).isEqualTo(1L);

        var events = clickEventRepository.countDailyClicksByCode(created.code());
        assertThat(events).hasSize(1);
        assertThat(events.get(0).getClickCount()).isEqualTo(1L);

        service.resolveAndRecordClick(created.code());
        assertThat(service.stats(created.code()).clicks()).isEqualTo(2L);
        assertThat(clickEventRepository.countDailyClicksByCode(created.code()).get(0).getClickCount())
                .isEqualTo(2L);
    }

    @Test
    void resolveUnknownCodeThrowsNotFound() {
        assertThatThrownBy(() -> service.resolveAndRecordClick("zzzzzzz"))
                .isInstanceOf(CodeNotFoundException.class);
    }

    @Test
    void statsUnknownCodeThrowsNotFound() {
        assertThatThrownBy(() -> service.stats("zzzzzzy"))
                .isInstanceOf(CodeNotFoundException.class);
    }

    @Test
    void statsDoesNotIncrementClickCount() {
        String url = "https://example.com/stats-no-increment-" + System.nanoTime();
        var created = service.shorten(url);

        service.stats(created.code());
        service.stats(created.code());

        assertThat(service.stats(created.code()).clicks()).isZero();
    }

    @Test
    void dailyStatsReturnsEmptyListForKnownCodeWithZeroClicks() {
        String url = "https://example.com/daily-zero-" + System.nanoTime();
        var created = service.shorten(url);

        assertThat(service.dailyStats(created.code())).isEmpty();
    }

    @Test
    void dailyStatsAggregatesMultipleClicksAcrossDaysDescending() {
        String url = "https://example.com/daily-multi-" + System.nanoTime();
        var created = service.shorten(url);

        service.resolveAndRecordClick(created.code());
        service.resolveAndRecordClick(created.code());

        var daily = service.dailyStats(created.code());
        assertThat(daily).hasSize(1);
        assertThat(daily.get(0).clicks()).isEqualTo(2L);
        assertThat(daily.get(0).date()).matches("\\d{4}-\\d{2}-\\d{2}");
    }

    @Test
    void dailyStatsUnknownCodeThrowsNotFound() {
        assertThatThrownBy(() -> service.dailyStats("zzzzzzx"))
                .isInstanceOf(CodeNotFoundException.class);
    }

    @Test
    void dailyStatsSyntacticallyInvalidCodeThrowsNotFound() {
        assertThatThrownBy(() -> service.dailyStats("!!!"))
                .isInstanceOf(CodeNotFoundException.class);
    }
}
