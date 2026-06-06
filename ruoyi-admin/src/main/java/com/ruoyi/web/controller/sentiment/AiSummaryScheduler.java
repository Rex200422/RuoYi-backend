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
 * 每半点（如 1:30, 2:30...）自动生成 AI 舆情简报。
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
     * 每半点触发（1:30, 2:30, 3:30...）。
     * 与爬虫错开：爬虫在整点启动，简报在半点生成。
     */
    @Scheduled(cron = "0 30 * * * *")
    public void generateHourlySummary() {
        if (generating) {
            log.info("[AI Summary] 已在生成中，跳过本次触发");
            return;
        }
        generating = true;
        log.info("[AI Summary] 异步触发简报生成...");

        CompletableFuture.runAsync(() -> {
            try {
                generator.generate(1);
            } catch (Exception e) {
                log.error("[AI Summary] 生成异常: {}", e.getMessage());
            } finally {
                generating = false;
            }
        }, executor);
    }
}
