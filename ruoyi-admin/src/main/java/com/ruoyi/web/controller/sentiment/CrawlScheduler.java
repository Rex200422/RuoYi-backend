package com.ruoyi.web.controller.sentiment;

import java.util.Date;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;

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
 * Runs every 60s. Key safety rules:
 *   1. Skip if this config already has a running crawl (prevents duplicate triggers)
 *   2. Max 3 concurrent crawls globally (Playwright is very heavy on CPU/memory)
 *   3. last_crawl_time updated on START (not just on success) to prevent re-trigger
 */
@Component
public class CrawlScheduler {
    private static final Logger log = LoggerFactory.getLogger(CrawlScheduler.class);

    /** Max concurrent crawls — 2-core HDD machine: 2 crawlers max (each Playwright spawns 10+ processes) */
    private static final int MAX_CONCURRENT = 2;

    @Autowired private ICrawlConfigService crawlConfigService;
    @Autowired private ICrawlLogService crawlLogService;

    /** Semaphore limits concurrent crawls */
    private final Semaphore crawlSemaphore = new Semaphore(MAX_CONCURRENT, true);

    /** Fixed thread pool — bounded to MAX_CONCURRENT threads */
    private final ExecutorService crawlExecutor = Executors.newFixedThreadPool(MAX_CONCURRENT);

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
            // Safety check 1: skip if this config already has a running crawl
            int runningForConfig = crawlLogService.selectRunningCountByConfigId(config.getId());
            if (runningForConfig > 0) {
                log.info("Skipping config id={} ({}) — already {} running crawl(s)",
                         config.getId(), config.getSiteName(), runningForConfig);
                continue;
            }

            // Safety check 2: skip if global concurrent limit reached
            if (crawlSemaphore.availablePermits() <= 0) {
                log.info("Skipping config id={} ({}) — max concurrent crawls ({}) reached",
                         config.getId(), config.getSiteName(), MAX_CONCURRENT);
                continue;
            }

            // Trigger crawl asynchronously on bounded thread pool
            CompletableFuture.runAsync(() -> triggerCrawl(config), crawlExecutor);
        }
    }

    /**
     * Trigger a crawl for the given config.
     * Acquires a semaphore permit before running, releases on completion.
     */
    public void triggerCrawl(CrawlConfig config) {
        // Acquire semaphore permit (blocks if all permits taken)
        if (!crawlSemaphore.tryAcquire()) {
            log.warn("Semaphore full, skipping config id={}", config.getId());
            return;
        }
        try {
            doCrawl(config);
        } finally {
            crawlSemaphore.release();
        }
    }

    private void doCrawl(CrawlConfig config) {
        log.info("Starting crawl for config id={}, site={}, keyword={}",
                 config.getId(), config.getSiteName(), config.getKeyword());

        // Update last_crawl_time FIRST to prevent re-trigger by next scheduler tick
        CrawlConfig updateConfig = new CrawlConfig();
        updateConfig.setId(config.getId());
        updateConfig.setLastCrawlTime(new Date());
        crawlConfigService.update(updateConfig);

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
        String escapedKeyword = config.getKeyword();
        int maxResults = config.getMaxResults() != null ? config.getMaxResults() : 10;

        // 创建日志目录并定义日志文件路径
        String logDir = "/root/workspace/RuoYi-backend/crawlers/logs";
        new java.io.File(logDir).mkdirs();
        String logFilePath = logDir + "/" + config.getSiteName() + "_" + crawlLog.getId() + ".log";

        String command = String.format(
            "python3 -u %s --config-id %d --keyword \"%s\" --max %d --log-id %d 2>&1",
            scriptPath, config.getId(), escapedKeyword, maxResults, crawlLog.getId()
        );

        try {
            ProcessBuilder pb = new ProcessBuilder("bash", "-c", command);
            Process process = pb.start();
            
            // 实时逐行捕获输出并写入日志文件
            StringBuilder output = new StringBuilder();
            java.io.FileWriter logWriter = null;
            try {
                logWriter = new java.io.FileWriter(logFilePath);
                java.io.BufferedReader reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(process.getInputStream()));
                String line;
                while ((line = reader.readLine()) != null) {
                    output.append(line).append("\n");
                    logWriter.write(line);
                    logWriter.write("\n");
                    logWriter.flush();
                }
                reader.close();
            } catch (Exception logEx) {
                log.error("Failed to write crawl log to {}: {}", logFilePath, logEx.getMessage(), logEx);
            } finally {
                if (logWriter != null) try { logWriter.close(); } catch (Exception ignored) {}
            }
            
            int exitCode = process.waitFor();
            log.info("Crawl log written to {} ({} bytes)", logFilePath, new java.io.File(logFilePath).length());

            if (exitCode == 0) {
                log.info("Crawl completed successfully for config id={}", config.getId());
            } else {
                String errorDetail = output.length() > 2000 
                    ? output.substring(output.length() - 2000) 
                    : output.toString();
                log.warn("Crawl script exited with code {} for config id={}: {}", 
                         exitCode, config.getId(), errorDetail.substring(0, Math.min(200, errorDetail.length())));
                CrawlLog failedLog = new CrawlLog();
                failedLog.setId(crawlLog.getId());
                failedLog.setStatus("failed");
                failedLog.setErrorMsg("Exit code " + exitCode + ": " + errorDetail.trim());
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
            case "tumblr": return "tumblr_spider.py";
            case "reddit": return "reddit_spider.py";
            case "threads": return "threads_spider.py";
            case "x": return "x_spider.py";
            case "youtube": return "youtube_spider.py";
            case "u.s. treasury":
            case "us treasury":
            case "treasury": return "treasury_spider.py";
            default: return siteName.toLowerCase().trim().replace(" ", "_") + "_spider.py";
        }
    }
}
