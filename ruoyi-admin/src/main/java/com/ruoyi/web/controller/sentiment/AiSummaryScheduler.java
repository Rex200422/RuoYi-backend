package com.ruoyi.web.controller.sentiment;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 每6小时整点（如 0:00, 6:00, 12:00, 18:00）自动生成 AI 舆情简报。
 * 异步非阻塞，不干扰爬虫任务和前端请求。
 */
@Component
public class AiSummaryScheduler {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryScheduler.class);

    @Autowired
    private AiSummaryGenerator generator;

    /** 单线程 + daemon，正在生成时跳过新触发 */
    private final ExecutorService executor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "ai-summary-worker");
        t.setDaemon(true);
        return t;
    });

    private volatile boolean generating = false;

    /**
     * 每6小时整点触发（0:00, 6:00, 12:00, 18:00）。
     */
    @Scheduled(cron = "0 0 */6 * * *")
    public void generateSixHourlySummary() {
        if (generating) {
            log.info("[AI Summary] 已在生成中，跳过本次触发");
            return;
        }
        generating = true;
        log.info("[AI Summary] 异步触发简报生成...");

        CompletableFuture.runAsync(() -> {
            try {
                generator.generate(24);
            } catch (Exception e) {
                log.error("[AI Summary] 生成异常: {}", e.getMessage());
            } finally {
                generating = false;
            }
        }, executor);
    }
}
