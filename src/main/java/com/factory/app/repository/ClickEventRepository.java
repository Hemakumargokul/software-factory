package com.factory.app.repository;

import com.factory.app.domain.ClickEvent;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

/**
 * Spring Data repository for {@link ClickEvent} rows.
 *
 * <p>Exposes a single aggregate query that groups events for a given code by
 * their UTC calendar date (derived from {@code clicked_at_utc}), counting
 * events per day and ordering by that date descending. All aggregation is
 * performed by the database; no counts are computed in application memory.
 */
public interface ClickEventRepository extends JpaRepository<ClickEvent, Long> {

    /**
     * Projection row for the daily aggregate query: a UTC calendar date
     * formatted as {@code yyyy-MM-dd} and the number of clicks recorded on
     * that date for the queried code.
     */
    interface DailyCount {
        String getClickDay();

        long getClickCount();
    }

    /**
     * Groups this code's click events by UTC calendar date and counts them,
     * ordered most-recent-date-first. Uses H2's {@code FORMATDATETIME} to
     * derive a UTC calendar-day string directly from the stored
     * {@code Instant}, so the grouping is independent of server/session time
     * zone.
     */
    @Query(value = "SELECT FORMATDATETIME(ce.clicked_at_utc, 'yyyy-MM-dd', 'UTC') AS click_day, "
            + "COUNT(*) AS click_count "
            + "FROM click_event ce "
            + "WHERE ce.code = :code "
            + "GROUP BY click_day "
            + "ORDER BY click_day DESC",
            nativeQuery = true)
    List<DailyCount> countDailyClicksByCode(@Param("code") String code);
}
