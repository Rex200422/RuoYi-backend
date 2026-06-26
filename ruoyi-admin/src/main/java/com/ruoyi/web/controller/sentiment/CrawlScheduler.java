package com.ruoyi.web.controller.sentiment;

import java.util.Date;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import jakarta.annotation.PostConstruct;
import com.ruoyi.system.domain.sentiment.CrawlConfig;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.service.sentiment.ICrawlConfigService;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

/**
 * Scheduler that automatically triggers crawls based on crawl_config settings.
 * Runs every 60s. Key safety rules:
 *   1. Skip if this config already has a running crawl (prevents duplicate triggers)
 *   2. Max 3 concurrent crawls globally (Playwright is very heavy on CPU/memory)
 *   3. last_crawl_time updated on completion (in finally block) to prevent re-trigger
 */
@Component
public class CrawlScheduler {
    @PostConstruct
    public void init() {
        // 服务启动时将所有 running 状态的任务标记为 failed
        CrawlLog query = new CrawlLog();
        query.setStatus("running");
        List<CrawlLog> runningLogs = crawlLogService.selectList(query);
        if (runningLogs != null && !runningLogs.isEmpty()) {
            for (CrawlLog logEntry : runningLogs) {
                CrawlLog update = new CrawlLog();
                update.setId(logEntry.getId());
                update.setStatus("failed");
                update.setErrorMsg("服务重启，任务被中断");
                update.setEndTime(new Date());
                crawlLogService.update(update);
                log.warn("标记 crawl_log id={} ({}) 为 failed — 服务重启", logEntry.getId(), logEntry.getSiteName());
            }
            log.info("启动时清理了 {} 条 running 状态的爬取任务", runningLogs.size());
        }
    }

    private static final Logger log = LoggerFactory.getLogger(CrawlScheduler.class);

    /** Max concurrent crawls — 2-core HDD machine: 2 crawlers max (each Playwright spawns 10+ processes) */
    private static final int MAX_CONCURRENT = 1;

    @Autowired private ICrawlConfigService crawlConfigService;
    @Autowired private ICrawlLogService crawlLogService;

    /** Semaphore limits concurrent crawls */
    private final Semaphore crawlSemaphore = new Semaphore(MAX_CONCURRENT, true);

    /** Fixed thread pool — bounded to MAX_CONCURRENT threads */
    private final ExecutorService crawlExecutor = Executors.newFixedThreadPool(MAX_CONCURRENT + 4);

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
            // Skip if this config already has a running or pending crawl
            int pendingOrRunning = crawlLogService.selectPendingOrRunningCountByConfigId(config.getId());
            if (pendingOrRunning > 0) {
                log.info("Skipping config id={} ({}) — already {} pending/running crawl(s)",
                         config.getId(), config.getSiteName(), pendingOrRunning);
                continue;
            }

            // Create pending record (visible in UI immediately)
            CrawlLog pendingLog = new CrawlLog();
            pendingLog.setSiteName(config.getSiteName());
            pendingLog.setKeyword(config.getKeyword());
            pendingLog.setStatus("pending");
            pendingLog.setConfigId(config.getId());
            pendingLog.setStartTime(new Date());
            crawlLogService.insert(pendingLog);

            // Submit to thread pool — blocks waiting for semaphore permit
            final Long pendingLogId = pendingLog.getId();
            CompletableFuture.runAsync(() -> triggerCrawl(config, pendingLogId), crawlExecutor);
        }
    }

    /**
     * Trigger a crawl for the given config.
     * Acquires a semaphore permit before running, releases on completion.
     */

    /**
     * Trigger all enabled crawl configs. Uses the bounded thread pool,
     * so concurrency is naturally limited by MAX_CONCURRENT.
     */
    public int triggerAllEnabled() {
        // 查询所有启用的配置，而非只查"到期"的（手动触发应强制执行）
        CrawlConfig query = new CrawlConfig();
        query.setEnabled(1);
        List<CrawlConfig> configs = crawlConfigService.selectList(query);
        if (configs == null || configs.isEmpty()) return 0;
        int triggered = 0;
        for (CrawlConfig config : configs) {
            // 跳过已经有 pending 或 running 任务的配置
            int pendingOrRunning = crawlLogService.selectPendingOrRunningCountByConfigId(config.getId());
            if (pendingOrRunning > 0) {
                log.info("Skipping config id={} — already {} pending/running task(s)", config.getId(), pendingOrRunning);
                continue;
            }
            // 插入 pending 状态的 crawl_log 记录（立即可见）
            CrawlLog pendingLog = new CrawlLog();
            pendingLog.setSiteName(config.getSiteName());
            pendingLog.setKeyword(config.getKeyword());
            pendingLog.setStatus("pending");
            pendingLog.setConfigId(config.getId());
            pendingLog.setStartTime(new Date());
            crawlLogService.insert(pendingLog);

            // 异步提交到线程池，由信号量控制并发排队
            final Long pendingLogId = pendingLog.getId();
            CompletableFuture.runAsync(() -> triggerCrawl(config, pendingLogId), crawlExecutor);
            triggered++;
        }
        return triggered;
    }

    /**
     * 手动触发单个平台爬取。
     * 与 triggerAllEnabled 相同的 pending 逻辑，但只处理一个配置。
     */
    public void triggerSingleCrawl(CrawlConfig config) {
        // 插入 pending 记录
        CrawlLog pendingLog = new CrawlLog();
        pendingLog.setSiteName(config.getSiteName());
        pendingLog.setKeyword(config.getKeyword());
        pendingLog.setStatus("pending");
        pendingLog.setConfigId(config.getId());
        pendingLog.setStartTime(new Date());
        crawlLogService.insert(pendingLog);

        final Long pendingLogId = pendingLog.getId();
        CompletableFuture.runAsync(() -> triggerCrawl(config, pendingLogId), crawlExecutor);
    }

    public int getMaxConcurrent() {
        return MAX_CONCURRENT;
    }
    public void triggerCrawl(CrawlConfig config, Long pendingLogId) {
        // Acquire semaphore permit — blocks until a slot is available (no timeout for pending tasks)
        try {
            crawlSemaphore.acquire();
            doCrawl(config, pendingLogId);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("Interrupted waiting for semaphore, config id={}", config.getId());
        } finally {
            crawlSemaphore.release();
        }
    }

    private void doCrawl(CrawlConfig config, Long pendingLogId) {
        log.info("Starting crawl for config id={}, site={}, keyword={}",
                 config.getId(), config.getSiteName(), config.getKeyword());

        // 如果有 pending 记录，更新为 running；否则新建
        CrawlLog crawlLog;
        if (pendingLogId != null) {
            crawlLog = crawlLogService.selectById(pendingLogId);
            if (crawlLog != null) {
                crawlLog.setStatus("running");
                crawlLog.setStartTime(new Date());
                crawlLogService.update(crawlLog);
            } else {
                crawlLog = new CrawlLog();
                crawlLog.setSiteName(config.getSiteName());
                crawlLog.setKeyword(config.getKeyword());
                crawlLog.setStatus("running");
                crawlLog.setStartTime(new Date());
                crawlLog.setConfigId(config.getId());
                crawlLogService.insert(crawlLog);
            }
        } else {
            crawlLog = new CrawlLog();
            crawlLog.setSiteName(config.getSiteName());
            crawlLog.setKeyword(config.getKeyword());
            crawlLog.setStatus("running");
            crawlLog.setStartTime(new Date());
            crawlLog.setConfigId(config.getId());
            crawlLogService.insert(crawlLog);
        }

        try {
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
                "/apps/miniconda3/bin/python3 -u %s --config-id %d --keyword \"%s\" --max %d --log-id %d 2>&1",
                scriptPath, config.getId(), escapedKeyword, maxResults, crawlLog.getId()
            );

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
            
            int exitCode;
            boolean finished = process.waitFor(60, TimeUnit.MINUTES);
            if (!finished) {
                log.warn("Crawl for config id={} exceeded 60min timeout, force killing", config.getId());
                process.destroyForcibly();
                exitCode = -1;
            } else {
                exitCode = process.exitValue();
            }
            log.info("Crawl log written to {} ({} bytes)", logFilePath, new java.io.File(logFilePath).length());

            if (exitCode == 0) {
                log.info("Crawl completed successfully for config id={}", config.getId());
            } else {
                String errorDetail = output.length() > 2000 
                    ? output.substring(output.length() - 2000) 
                    : output.toString();
                String timeoutSuffix = (exitCode == -1) ? " [超时: 爬取运行超过60分钟，已强制终止]" : "";
                log.warn("Crawl script exited with code {} for config id={}: {}", 
                         exitCode, config.getId(), errorDetail.substring(0, Math.min(200, errorDetail.length())));
                CrawlLog failedLog = new CrawlLog();
                failedLog.setId(crawlLog.getId());
                failedLog.setStatus("failed");
                failedLog.setErrorMsg("Exit code " + exitCode + ": " + errorDetail.trim() + timeoutSuffix);
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
        } finally {
            // 任务结束时（成功/失败/异常）更新 last_crawl_time
            CrawlConfig updateConfig = new CrawlConfig();
            updateConfig.setId(config.getId());
            updateConfig.setLastCrawlTime(new Date());
            crawlConfigService.update(updateConfig);
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
