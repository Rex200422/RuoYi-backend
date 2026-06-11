package com.ruoyi.web.controller.sentiment;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class TrendCollector {
    private static final Logger log = LoggerFactory.getLogger(TrendCollector.class);
    
    @Autowired
    private JdbcTemplate jdbc;
    
    // 分类列表
    private static final String[] EVENT_CATEGORIES = {
        "军事", "贸易", "外交", "科技", "人权",
        "社会", "经济", "政治", "台海", "港澳",
        "南海", "网络安全", "军售", "制裁", "能源",
        "教育", "环境", "金融", "移民", "其他"
    };
    
    /**
     * 收集当前时间点的趋势数据并写入数据库。
     * 每小时调用一次，记录该时刻各分类/平台的累计事件数。
     */
    public void collect() {
        LocalDateTime now = LocalDateTime.now();
        log.info("[Trend] 开始统计趋势数据...");
        
        try {
            collectCategoryTrends(now);
            collectPlatformTrends(now);
            log.info("[Trend] 趋势数据统计完成");
        } catch (Exception e) {
            log.error("[Trend] 趋势统计失败: {}", e.getMessage());
        }
    }
    
    /**
     * 统计各分类的累计事件数（基于category字段）
     * category字段由4B预摘要模型填充，fallback用classifyByKeywords
     */
    private void collectCategoryTrends(LocalDateTime now) {
        // 统计social_post的分类分布
        List<Map<String, Object>> postCounts = jdbc.queryForList(
            "SELECT COALESCE(category, '其他') AS cat, COUNT(*) AS cnt FROM social_post GROUP BY cat"
        );
        // 统计news_article的分类分布
        List<Map<String, Object>> newsCounts = jdbc.queryForList(
            "SELECT COALESCE(category, '其他') AS cat, COUNT(*) AS cnt FROM news_article GROUP BY cat"
        );
        
        Map<String, Integer> totalCounts = new LinkedHashMap<>();
        for (Map<String, Object> row : postCounts) {
            String cat = str(row.get("cat"));
            int cnt = row.get("cnt") instanceof Number ? ((Number) row.get("cnt")).intValue() : 0;
            totalCounts.merge(cat, cnt, Integer::sum);
        }
        for (Map<String, Object> row : newsCounts) {
            String cat = str(row.get("cat"));
            int cnt = row.get("cnt") instanceof Number ? ((Number) row.get("cnt")).intValue() : 0;
            totalCounts.merge(cat, cnt, Integer::sum);
        }
        
        // 写入DB
        for (Map.Entry<String, Integer> entry : totalCounts.entrySet()) {
            jdbc.update(
                "INSERT INTO category_trend (record_time, category, event_count) VALUES (?, ?, ?)",
                Timestamp.valueOf(now), entry.getKey(), entry.getValue()
            );
        }
        log.info("[Trend] 分类趋势: {}个分类", totalCounts.size());
    }
    
    /**
     * 统计各平台的累计事件数
     */
    private void collectPlatformTrends(LocalDateTime now) {
        // social_post按site_name统计
        List<Map<String, Object>> postCounts = jdbc.queryForList(
            "SELECT site_name AS platform, COUNT(*) AS cnt FROM social_post GROUP BY site_name"
        );
        // news_article按source统计
        List<Map<String, Object>> newsCounts = jdbc.queryForList(
            "SELECT source AS platform, COUNT(*) AS cnt FROM news_article GROUP BY source"
        );
        
        Map<String, Integer> totalCounts = new LinkedHashMap<>();
        for (Map<String, Object> row : postCounts) {
            String platform = str(row.get("platform"));
            int cnt = row.get("cnt") instanceof Number ? ((Number) row.get("cnt")).intValue() : 0;
            totalCounts.merge(platform, cnt, Integer::sum);
        }
        for (Map<String, Object> row : newsCounts) {
            String platform = str(row.get("platform"));
            int cnt = row.get("cnt") instanceof Number ? ((Number) row.get("cnt")).intValue() : 0;
            totalCounts.merge(platform, cnt, Integer::sum);
        }
        
        // 写入DB
        for (Map.Entry<String, Integer> entry : totalCounts.entrySet()) {
            jdbc.update(
                "INSERT INTO platform_trend (record_time, platform, event_count) VALUES (?, ?, ?)",
                Timestamp.valueOf(now), entry.getKey(), entry.getValue()
            );
        }
        log.info("[Trend] 平台趋势: {}个平台", totalCounts.size());
    }
    
    private String str(Object o) { return o != null ? o.toString().trim() : ""; }
}
