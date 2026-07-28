package com.factory.app.repository;

import static org.assertj.core.api.Assertions.assertThat;

import com.factory.app.domain.ClickEvent;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;

@DataJpaTest
class ClickEventRepositoryTest {

    @Autowired
    private ClickEventRepository repository;

    @Test
    void countDailyClicksByCodeReturnsEmptyWhenNoEvents() {
        assertThat(repository.countDailyClicksByCode("nope000")).isEmpty();
    }

    @Test
    void countDailyClicksByCodeGroupsAcrossUtcDayBoundariesInDescendingOrder() {
        String code = "day0001";

        // Day 1: two events, one just after UTC midnight.
        Instant day1Start = Instant.parse("2024-03-10T00:00:01Z");
        Instant day1Late = Instant.parse("2024-03-10T23:59:59Z");

        // Day 2: three events, one right at the UTC midnight boundary and one
        // one second before the *next* midnight, to prove the boundary itself
        // (not just "close to midnight") buckets correctly.
        Instant day2Midnight = Instant.parse("2024-03-11T00:00:00Z");
        Instant day2Mid = Instant.parse("2024-03-11T12:00:00Z");
        Instant day2Late = Instant.parse("2024-03-11T23:59:59Z");

        // Day 3: a single event just after midnight, one second after day 2's
        // last event - confirms consecutive-second events straddling
        // midnight land in different buckets.
        Instant day3Start = Instant.parse("2024-03-12T00:00:00Z");

        // Unrelated code, must not be included in the aggregation for `code`.
        repository.saveAndFlush(new ClickEvent("other01", day2Mid));

        repository.saveAndFlush(new ClickEvent(code, day1Start));
        repository.saveAndFlush(new ClickEvent(code, day1Late));
        repository.saveAndFlush(new ClickEvent(code, day2Midnight));
        repository.saveAndFlush(new ClickEvent(code, day2Mid));
        repository.saveAndFlush(new ClickEvent(code, day2Late));
        repository.saveAndFlush(new ClickEvent(code, day3Start));

        var results = repository.countDailyClicksByCode(code);

        assertThat(results).hasSize(3);

        assertThat(results.get(0).getClickDay()).isEqualTo("2024-03-12");
        assertThat(results.get(0).getClickCount()).isEqualTo(1L);

        assertThat(results.get(1).getClickDay()).isEqualTo("2024-03-11");
        assertThat(results.get(1).getClickCount()).isEqualTo(3L);

        assertThat(results.get(2).getClickDay()).isEqualTo("2024-03-10");
        assertThat(results.get(2).getClickCount()).isEqualTo(2L);
    }

    @Test
    void countDailyClicksByCodeIgnoresEventsFromOtherCodes() {
        Instant now = Instant.parse("2024-05-01T10:00:00Z");
        repository.saveAndFlush(new ClickEvent("codeA01", now));
        repository.saveAndFlush(new ClickEvent("codeB01", now));
        repository.saveAndFlush(new ClickEvent("codeB01", now.plus(1, ChronoUnit.HOURS)));

        var resultsA = repository.countDailyClicksByCode("codeA01");
        var resultsB = repository.countDailyClicksByCode("codeB01");

        assertThat(resultsA).hasSize(1);
        assertThat(resultsA.get(0).getClickCount()).isEqualTo(1L);

        assertThat(resultsB).hasSize(1);
        assertThat(resultsB.get(0).getClickCount()).isEqualTo(2L);
    }
}
