package com.ruoyi.web.controller.sentiment;

import com.ruoyi.system.domain.sentiment.HighFrequencyUser;
import com.ruoyi.system.domain.sentiment.UserCrossProfile;
import com.ruoyi.system.service.sentiment.IHighFrequencyUserService;
import com.ruoyi.system.service.sentiment.IUserCrossProfileService;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import jakarta.annotation.PostConstruct;
import java.io.*;
import java.util.*;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

@Component
public class CrossProfileScheduler {

    private static final Logger log = LoggerFactory.getLogger(CrossProfileScheduler.class);
    private static final String PYTHON = "/apps/miniconda3/bin/python3";
    private static final String SPIDER_DIR = "/root/workspace/RuoYi-backend/crawlers";

    @Autowired private IHighFrequencyUserService hfUserService;
    @Autowired private IUserCrossProfileService profileService;
    @Autowired private org.springframework.jdbc.core.JdbcTemplate jdbcTemplate;

    private final ObjectMapper mapper = new ObjectMapper();
    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    @PostConstruct
    public void init() {
        log.info("CrossProfileScheduler initialized");
    }

    /**
     * 每天凌晨2点执行：
     * 1. 统计3天内高频用户 → high_frequency_user 表
     * 2. 取 top10 → 调用 Python 提取跨平台信息
     * 3. 存入 user_cross_profile 表
     */
    @Scheduled(cron = "0 0 2 * * ?")
    public void runDailyProfile() {
        log.info("=== CrossProfile daily task started ===");
        try {
            // Step 1: 统计3天内高频用户
            collectHighFrequencyUsers();
            log.info("Step 1 done: high_frequency_user updated");

            // Step 2: 取 top10（去重用户名，取最高频的）
            List<Map<String, Object>> topUsers = jdbcTemplate.queryForList(
                "SELECT username, SUM(post_count) as total_posts " +
                "FROM high_frequency_user " +
                "WHERE window_start >= DATE_SUB(NOW(), INTERVAL 3 DAY) " +
                "GROUP BY username " +
                "ORDER BY total_posts DESC " +
                "LIMIT 10"
            );
            log.info("Step 2 done: found {} top users", topUsers.size());

            // Step 3: 逐个调用 Python 提取
            for (Map<String, Object> user : topUsers) {
                String username = (String) user.get("username");
                processUser(username);
            }

        } catch (Exception e) {
            log.error("CrossProfile daily task failed", e);
        }
        log.info("=== CrossProfile daily task completed ===");
    }

    /** 统计3天内高频用户 */
    private void collectHighFrequencyUsers() {
        Date now = new Date();
        Date windowStart = new Date(now.getTime() - 3L * 24 * 3600 * 1000);

        // 从 social_post 表按平台统计高频用户
        List<Map<String, Object>> stats = jdbcTemplate.queryForList(
            "SELECT author, site_name, COUNT(*) as cnt " +
            "FROM social_post " +
            "WHERE create_time >= ? AND author IS NOT NULL AND author != '' " +
            "GROUP BY author, site_name " +
            "HAVING cnt >= 3 " +
            "ORDER BY cnt DESC",
            windowStart
        );

        List<HighFrequencyUser> users = new ArrayList<>();
        for (Map<String, Object> row : stats) {
            HighFrequencyUser hf = new HighFrequencyUser();
            hf.setUsername((String) row.get("author"));
            hf.setPlatform((String) row.get("site_name"));
            hf.setPostCount(((Number) row.get("cnt")).intValue());
            hf.setWindowStart(windowStart);
            hf.setWindowEnd(now);
            users.add(hf);
        }

        if (!users.isEmpty()) {
            // 先清理旧数据
            hfUserService.deleteByWindowStartBefore(windowStart);
            hfUserService.batchInsertOrUpdate(users);
            log.info("Saved {} high frequency users", users.size());
        }
    }

    /** 调用 Python 脚本处理单个用户 */
    private void processUser(String username) {
        log.info("Processing user: {}", username);

        try {
            // 检查是否已查询过
            UserCrossProfile existing = profileService.selectByUsername(username);
            if (existing != null) {
                log.info("User {} already profiled, skipping", username);
                return;
            }

            // 构建命令
            List<String> cmd = Arrays.asList(
                PYTHON, SPIDER_DIR + "/cross_profile_spider.py",
                "--username", username,
                "--config-id", "99",
                "--log-id", "0"
            );

            ProcessBuilder pb = new ProcessBuilder(cmd);
            pb.directory(new File(SPIDER_DIR));
            pb.redirectErrorStream(true);

            Process process = pb.start();

            // 读取输出
            StringBuilder output = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    output.append(line).append("\n");
                }
            }

            boolean finished = process.waitFor(300, java.util.concurrent.TimeUnit.SECONDS);

            if (!finished) {
                process.destroyForcibly();
                saveErrorResult(username, "Timeout after 300s");
                return;
            }

            // 解析 JSON 结果文件
            String jsonPath = SPIDER_DIR + "/logs/cross_" + username + "_0.json";
            File jsonFile = new File(jsonPath);
            if (!jsonFile.exists()) {
                saveErrorResult(username, "JSON output file not found: " + jsonPath);
                return;
            }

            JsonNode root = mapper.readTree(jsonFile);
            JsonNode platforms = root.get("platforms");
            int claimedCount = root.get("claimed_count").asInt();

            // 构建 UserCrossProfile
            UserCrossProfile profile = new UserCrossProfile();
            profile.setUsername(username);
            profile.setQueryTime(new Date());
            profile.setClaimedCount(claimedCount);
            profile.setRawResult(mapper.writeValueAsString(root));
            profile.setErrorMsg(null);

            // 设置各平台数据
            setPlatformData(profile, platforms, "reddit");
            setPlatformData(profile, platforms, "instagram");
            setPlatformData(profile, platforms, "tiktok");
            setPlatformData(profile, platforms, "twitter");
            setPlatformData(profile, platforms, "twitch");
            setPlatformData(profile, platforms, "tumblr");
            setPlatformData(profile, platforms, "telegram");

            profileService.insert(profile);
            log.info("Saved profile for {}, claimed={}", username, claimedCount);

            // 清理 JSON 文件
            jsonFile.delete();

        } catch (Exception e) {
            log.error("Failed to process user {}: {}", username, e.getMessage());
            saveErrorResult(username, e.getMessage());
        }
    }

    private void setPlatformData(UserCrossProfile profile, JsonNode platforms, String platformKey) {
        JsonNode platformData = platforms.get(platformKey);
        if (platformData == null) return;

        String status = platformData.has("status") ? platformData.get("status").asText("unknown") : "unknown";
        String data = platformData.has("data") ? platformData.get("data").toString() : "{}";

        switch (platformKey) {
            case "reddit": profile.setRedditStatus(status); profile.setRedditData(data); break;
            case "instagram": profile.setInstagramStatus(status); profile.setInstagramData(data); break;
            case "tiktok": profile.setTiktokStatus(status); profile.setTiktokData(data); break;
            case "twitter": profile.setTwitterStatus(status); profile.setTwitterData(data); break;
            case "twitch": profile.setTwitchStatus(status); profile.setTwitchData(data); break;
            case "tumblr": profile.setTumblrStatus(status); profile.setTumblrData(data); break;
            case "telegram": profile.setTelegramStatus(status); profile.setTelegramData(data); break;
        }
    }

    private void saveErrorResult(String username, String errorMsg) {
        try {
            UserCrossProfile profile = new UserCrossProfile();
            profile.setUsername(username);
            profile.setQueryTime(new Date());
            profile.setClaimedCount(0);
            profile.setErrorMsg(errorMsg);
            profileService.insert(profile);
        } catch (Exception e) {
            log.error("Failed to save error result for {}", username);
        }
    }

    /** 手动触发：指定用户名查询 */
    public void triggerManual(String username) {
        CompletableFuture.runAsync(() -> processUser(username), executor);
    }
}
