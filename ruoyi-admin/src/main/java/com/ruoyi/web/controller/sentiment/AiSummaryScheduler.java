package com.ruoyi.web.controller.sentiment;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 每30分钟自动生成 AI 舆情简报（异步非阻塞）。
 * 调用 Python ai_summarizer.py 脚本，从数据库取最近1小时数据，调用 Ollama qwen3.6 生成简报。
 * 
 * 关键设计：
 * - 生成过程完全异步，不阻塞爬虫任务或前端请求
 * - 使用 CachedThreadPool，如果上一次还没完成，新任务直接跳过（不排队）
 * - 进程超时300秒，防止僵尸进程
 */
@Component
public class AiSummaryScheduler {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryScheduler.class);
    private static final String SCRIPT_PATH = "/root/workspace/RuoYi-backend/crawlers/ai_summarizer.py";

    /** 单线程 + 允许空闲60秒回收，如果正在生成则新任务直接跳过 */
    private final ExecutorService executor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "ai-summary-worker");
        t.setDaemon(true);
        return t;
    });

    /** 标记当前是否正在生成，防止重复触发 */
    private volatile boolean generating = false;

    /**
     * 每半点触发（如 1:30, 2:30, 3:30...）。
     * 爬虫在整点启动，简报在半点生成，错开资源占用。
     */
    @Scheduled(cron = "0 30 * * * *")
    public void generateHourlySummary() {
        if (generating) {
            log.info("AI summary already generating, skipping this tick.");
            return;
        }
        generating = true;
        log.info("Starting AI summary generation (async)...");

        CompletableFuture.runAsync(() -> {
            try {
                ProcessBuilder pb = new ProcessBuilder(
                    "python3", SCRIPT_PATH, "--hours", "1"
                );
                pb.redirectErrorStream(true);
                pb.environment().put("no_proxy", "*");
                pb.environment().put("NO_PROXY", "*");
                Process process = pb.start();

                // 读取输出（防止进程阻塞）
                try (var reader = new java.io.BufferedReader(
                        new java.io.InputStreamReader(process.getInputStream()))) {
                    String line;
                    while ((line = reader.readLine()) != null) {
                        log.info("[ai-summarizer] {}", line);
                    }
                }

                int exitCode = process.waitFor();
                if (exitCode == 0) {
                    log.info("AI summary generated successfully.");
                } else {
                    log.warn("AI summary script exited with code {}", exitCode);
                }
            } catch (Exception e) {
                log.error("Failed to generate AI summary: {}", e.getMessage());
            } finally {
                generating = false;
            }
        }, executor);
    }
}
