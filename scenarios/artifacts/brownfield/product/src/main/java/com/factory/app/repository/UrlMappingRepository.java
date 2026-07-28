package com.factory.app.repository;

import com.factory.app.domain.UrlMapping;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

/**
 * Spring Data repository for {@link UrlMapping} rows. Both lookup methods hit
 * indexed, uniquely-constrained columns.
 */
public interface UrlMappingRepository extends JpaRepository<UrlMapping, Long> {

    Optional<UrlMapping> findByCode(String code);

    Optional<UrlMapping> findByOriginalUrl(String originalUrl);
}
