package com.factory.app;

import com.factory.app.config.DataDirectorySupport;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.event.ApplicationEnvironmentPreparedEvent;
import org.springframework.context.ApplicationListener;

@SpringBootApplication
public class AppApplication {

    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(AppApplication.class);
        // Ensure the H2 file-based data directory exists before any
        // datasource/connection pool bean is created during context refresh.
        // Hooking the environment-prepared event (rather than doing this
        // before SpringApplication.run) guarantees profile-specific property
        // overrides (e.g. a test profile) have already been resolved.
        app.addListeners((ApplicationListener<ApplicationEnvironmentPreparedEvent>) event ->
                DataDirectorySupport.ensureDirectoryExists(
                        event.getEnvironment().getProperty("spring.datasource.url")));
        app.run(args);
    }
}
