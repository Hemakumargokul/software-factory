package com.factory.app.repository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.factory.app.domain.UrlMapping;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.dao.DataIntegrityViolationException;

@DataJpaTest
class UrlMappingRepositoryTest {

    @Autowired
    private UrlMappingRepository repository;

    @Test
    void savesAndFindsByCode() {
        repository.saveAndFlush(new UrlMapping("abc1234", "https://example.com/one"));

        var found = repository.findByCode("abc1234");

        assertThat(found).isPresent();
        assertThat(found.get().getOriginalUrl()).isEqualTo("https://example.com/one");
        assertThat(found.get().getClickCount()).isZero();
    }

    @Test
    void savesAndFindsByOriginalUrl() {
        repository.saveAndFlush(new UrlMapping("abc1235", "https://example.com/two"));

        var found = repository.findByOriginalUrl("https://example.com/two");

        assertThat(found).isPresent();
        assertThat(found.get().getCode()).isEqualTo("abc1235");
    }

    @Test
    void findByCodeReturnsEmptyWhenMissing() {
        assertThat(repository.findByCode("nope000")).isEmpty();
    }

    @Test
    void findByOriginalUrlReturnsEmptyWhenMissing() {
        assertThat(repository.findByOriginalUrl("https://example.com/missing")).isEmpty();
    }

    @Test
    void enforcesUniqueCodeConstraint() {
        repository.saveAndFlush(new UrlMapping("dup0001", "https://example.com/first"));

        assertThatThrownBy(() ->
                repository.saveAndFlush(new UrlMapping("dup0001", "https://example.com/second")))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void enforcesUniqueOriginalUrlConstraint() {
        repository.saveAndFlush(new UrlMapping("code001", "https://example.com/same"));

        assertThatThrownBy(() ->
                repository.saveAndFlush(new UrlMapping("code002", "https://example.com/same")))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    @Test
    void originalUrlLookupIsExactCaseSensitiveMatch() {
        repository.saveAndFlush(new UrlMapping("code003", "https://Example.com/Path"));

        assertThat(repository.findByOriginalUrl("https://example.com/path")).isEmpty();
        assertThat(repository.findByOriginalUrl("https://Example.com/Path")).isPresent();
    }

    @Test
    void clickCountCanBeIncrementedAndPersisted() {
        UrlMapping saved = repository.saveAndFlush(new UrlMapping("code004", "https://example.com/click"));
        saved.incrementClickCount();
        repository.saveAndFlush(saved);

        var found = repository.findByCode("code004");
        assertThat(found).isPresent();
        assertThat(found.get().getClickCount()).isEqualTo(1L);
    }
}
