package com.factory.app.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;

/**
 * Durable record of a single successful redirect ("click") for a short code.
 * One row is inserted per successful resolution in
 * {@code UrlShortenerService#resolveAndRecordClick}, independent of the
 * {@link UrlMapping#getClickCount()} counter, so that per-day click history
 * can be recomputed from storage at any time (including across restarts).
 */
@Entity
@Table(
        name = "click_event",
        indexes = {
                @Index(name = "idx_click_event_code", columnList = "code")
        }
)
public class ClickEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "code", nullable = false, length = 7)
    private String code;

    @Column(name = "clicked_at_utc", nullable = false)
    private Instant clickedAtUtc;

    protected ClickEvent() {
        // JPA
    }

    public ClickEvent(String code, Instant clickedAtUtc) {
        this.code = code;
        this.clickedAtUtc = clickedAtUtc;
    }

    public Long getId() {
        return id;
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public Instant getClickedAtUtc() {
        return clickedAtUtc;
    }

    public void setClickedAtUtc(Instant clickedAtUtc) {
        this.clickedAtUtc = clickedAtUtc;
    }
}
