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
 * 每小时统计趋势数据，写入category_trend和platform_trend表。
 * 独立于简报生成，数据持久化存储。
 */
@Component
public class TrendScheduler {
    private static final Logger log = LoggerFactory.getLogger(TrendScheduler.class);
    
    @Autowired
    private TrendCollector collector;
    
    private final ExecutorService executor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "trend-collector");
        t.setDaemon(true);
        return t;
    });
    
    /**
     * 每小时整点触发（0:00, 1:00, 2:00, ..., 23:00）
     */
    @Scheduled(cron = "0 0 * * * *")
    public void collectHourlyTrends() {
        CompletableFuture.runAsync(() -> {
            try {
                collector.collect();
            } catch (Exception e) {
                log.error("[Trend] 统计异常: {}", e.getMessage());
            }
        }, executor);
    }
}
