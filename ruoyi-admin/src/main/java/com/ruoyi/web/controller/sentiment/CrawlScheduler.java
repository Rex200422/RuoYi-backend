package com.ruoyi.web.controller.sentiment;

import java.util.Date;
import java.util.List;
import java.util.concurrent.CompletableFuture;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import com.ruoyi.system.domain.sentiment.CrawlConfig;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.service.sentiment.ICrawlConfigService;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

/**
 * Scheduler that automatically triggers crawls based on crawl_config settings.
 * Runs every minute and checks for enabled configs that are due for crawling.
 * 
 * Before running: inserts a crawl_log entry with status='running'.
 * The Python script is responsible for updating the log entry when done.
 * After successful crawl: updates last_crawl_time in crawl_config.
 */
@Component
public class CrawlScheduler {
    private static final Logger log = LoggerFactory.getLogger(CrawlScheduler.class);

    @Autowired private ICrawlConfigService crawlConfigService;
    @Autowired private ICrawlLogService crawlLogService;

    /**
     * Check every minute for due crawl configs and trigger them.
     */
    @Scheduled(fixedRate = 60000)
    public void checkAndRunDueCrawls() {
        log.debug("Checking for due crawl configs...");

        List<CrawlConfig> dueConfigs = crawlConfigService.selectDueConfigs();
        if (dueConfigs == null || dueConfigs.isEmpty()) {
            log.debug("No due crawl configs found.");
            return;
        }

        log.info("Found {} due crawl config(s) to run.", dueConfigs.size());

        for (CrawlConfig config : dueConfigs) {
            // Run each crawl asynchronously using CompletableFuture
            CompletableFuture.runAsync(() -> triggerCrawl(config));
        }
    }

    /**
     * Trigger a crawl for the given config.
     * Creates a crawl_log entry and runs the Python script as a subprocess.
     */
    public void triggerCrawl(CrawlConfig config) {
        log.info("Starting crawl for config id={}, site={}, keyword={}", 
                 config.getId(), config.getSiteName(), config.getKeyword());

        // Insert crawl_log entry with status=running
        CrawlLog crawlLog = new CrawlLog();
        crawlLog.setSiteName(config.getSiteName());
        crawlLog.setKeyword(config.getKeyword());
        crawlLog.setStatus("running");
        crawlLog.setStartTime(new Date());
        crawlLog.setConfigId(config.getId());
        crawlLogService.insert(crawlLog);

        String scriptFile = getSiteScript(config.getSiteName());
        if (scriptFile == null) {
            log.warn("No script mapping for site: {}", config.getSiteName());
            CrawlLog failedLog = new CrawlLog();
            failedLog.setId(crawlLog.getId());
            failedLog.setStatus("failed");
            failedLog.setErrorMsg("No script mapping for site: " + config.getSiteName());
            failedLog.setEndTime(new Date());
            crawlLogService.update(failedLog);
            return;
        }

        String scriptPath = "/root/workspace/RuoYi-backend/crawlers/" + scriptFile;
        String escapedKeyword = config.getKeyword().replace("\"", "\\\"");
        int maxResults = config.getMaxResults() != null ? config.getMaxResults() : 10;

        String command = String.format(
            "python3 %s --config-id %d --keyword \"%s\" --max %d --log-id %d",
            scriptPath, config.getId(), escapedKeyword, maxResults, crawlLog.getId()
        );

        try {
            ProcessBuilder pb = new ProcessBuilder("bash", "-c", command);
            pb.redirectErrorStream(true);
            Process process = pb.start();
            int exitCode = process.waitFor();
            
            if (exitCode == 0) {
                log.info("Crawl completed successfully for config id={}", config.getId());
                // Update last_crawl_time in crawl_config
                CrawlConfig updateConfig = new CrawlConfig();
                updateConfig.setId(config.getId());
                updateConfig.setLastCrawlTime(new Date());
                crawlConfigService.update(updateConfig);
            } else {
                log.warn("Crawl script exited with code {} for config id={}", exitCode, config.getId());
                CrawlLog failedLog = new CrawlLog();
                failedLog.setId(crawlLog.getId());
                failedLog.setStatus("failed");
                failedLog.setErrorMsg("Script exited with code " + exitCode);
                failedLog.setEndTime(new Date());
                crawlLogService.update(failedLog);
            }
        } catch (Exception e) {
            log.error("Failed to run crawl script for config id={}: {}", config.getId(), e.getMessage());
            CrawlLog failedLog = new CrawlLog();
            failedLog.setId(crawlLog.getId());
            failedLog.setStatus("failed");
            failedLog.setErrorMsg("Script execution error: " + e.getMessage());
            failedLog.setEndTime(new Date());
            crawlLogService.update(failedLog);
        }
    }

    /**
     * Map site name to Python spider script filename.
     */
    private String getSiteScript(String siteName) {
        if (siteName == null) return null;
        switch (siteName.toLowerCase().trim()) {
            case "bluesky": return "bluesky_spider.py";
            case "cnn": return "cnn_spider.py";
            case "u.s. treasury":
            case "us treasury":
            case "treasury": return "treasury_spider.py";
            default: return siteName.toLowerCase().trim().replace(" ", "_") + "_spider.py";
        }
    }
}
