package com.ruoyi.web.controller.sentiment;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * AI 舆情简报生成器 — 纯 Java 实现
 * 从数据库取数据 → 预处理 → 调用 Ollama qwen3.6 → 存入 ai_summary 表
 */
@Service
public class AiSummaryGenerator {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryGenerator.class);

    private static final String OLLAMA_BASE = "http://200m.frpee.com:18138";
    private static final String MODEL_NAME = "qwen3.6:latest";

    // 预处理限制
    private static final int MAX_NEWS = 50;
    private static final int MAX_POSTS = 80;
    private static final int MAX_COMMENTS = 30;
    private static final int MAX_CONTENT_LEN = 150;
    private static final double TOKEN_PER_CHAR = 1.5;  // Chinese ~1.5 tokens/char
    private static final int MAX_INPUT_TOKENS = 28000;  // model 32k, reserve 4k

    // Token 预算分配
    private static final double NEWS_RATIO = 0.40;
    private static final double POST_RATIO = 0.35;
    private static final double COMMENT_RATIO = 0.15;
    private static final double PREV_RATIO = 0.10;

    @Autowired
    private JdbcTemplate jdbc;

    private final ObjectMapper mapper = new ObjectMapper();

    /**
     * 生成简报的完整流程
     * @return true 成功, false 失败
     */
    public boolean generate(int hours) {
        log.info("[AI Summary] === 开始生成简报 ===");

        // 1. 检查 Ollama
        if (!checkOllama()) {
            log.warn("[AI Summary] Ollama 不可用，跳过");
            return false;
        }

        // 2. 时间范围
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime dataEnd = now;

        // 3. 拉取最近数据(不限时间)
        List<Map<String, Object>> news = fetchNews();
        List<Map<String, Object>> posts = fetchPosts();
        List<Map<String, Object>> comments = fetchComments();
        Map<String, Object> prevSummary = fetchPreviousSummary();
        log.info("[AI Summary] 新闻: {} 条, 帖子: {} 条, 评论: {} 条", news.size(), posts.size(), comments.size());

        if (news.isEmpty() && posts.isEmpty()) {
            log.warn("[AI Summary] 无新数据，跳过");
            return false;
            }
        }

        // 4. 预处理 + 构建 prompt
        String dataSection = buildDataSection(news, posts, comments, prevSummary);
        String prevSection = buildPrevSection(prevSummary);
        int newsCount = countSection(dataSection, "新闻文章");
        int postCount = countSection(dataSection, "社交帖子");
        int commentCount = countSection(dataSection, "热门评论");

        String prompt = buildPrompt(hours, newsCount, postCount, commentCount, prevSection, dataSection);
        log.info("[AI Summary] Prompt 预估 tokens: {}", estimateTokens(prompt));

        // 5. 调用 Ollama
        long startMs = System.currentTimeMillis();
        String content = callOllama(prompt);
        if (content == null) {
            log.warn("[AI Summary] Ollama 调用失败");
            return false;
        }
        int genSeconds = (int) ((System.currentTimeMillis() - startMs) / 1000);
        log.info("[AI Summary] 生成完成 ({}s, {} chars)", genSeconds, content.length());

        // 6. 解析标题和风险等级
        String title = extractTitle(content);
        String riskLevel = extractRisk(content);
        log.info("[AI Summary] 标题: {}", title);
        log.info("[AI Summary] 风险等级: {}", riskLevel);

        // 7. 存入数据库
        saveSummary(title, content, riskLevel, dataEnd, dataEnd, newsCount, postCount, genSeconds);
        log.info("[AI Summary] === 完成 ===");
        return true;
    }

    // ==================== 数据获取 ====================

    private List<Map<String, Object>> fetchNews() {
        return jdbc.queryForList(
            "SELECT title, source, keywords, content, publish_date " +
            "FROM news_article ORDER BY crawl_time DESC LIMIT ?", MAX_NEWS
        );
    }

    private List<Map<String, Object>> fetchPosts() {
        return jdbc.queryForList(
            "SELECT title, author, site_name, like_count, comment_count, content, trigger_keyword " +
            "FROM social_post ORDER BY crawl_time DESC LIMIT ?", MAX_POSTS
        );
    }

    private List<Map<String, Object>> fetchComments() {
        return jdbc.queryForList(
            "SELECT sc.commenter, sc.comment_content, sc.like_count AS comment_likes, " +
            "sc.comment_time, sp.title AS post_title, sp.author AS post_author, sp.site_name " +
            "FROM social_comment sc JOIN social_post sp ON sc.post_id = sp.post_id " +
            "ORDER BY sc.like_count DESC, sc.crawl_time DESC LIMIT ?", MAX_COMMENTS
        );
    }

    private Map<String, Object> fetchPreviousSummary() {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT title, risk_level, content, news_count, social_count, " +
            "DATE_FORMAT(create_time, '%m-%d %H:%i') AS create_time " +
            "FROM ai_summary ORDER BY id DESC LIMIT 1"
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ==================== 数据预处理 ====================

    private String buildDataSection(List<Map<String, Object>> news,
                                     List<Map<String, Object>> posts,
                                     List<Map<String, Object>> comments,
                                     Map<String, Object> prev) {
        StringBuilder sb = new StringBuilder();
        int totalTokens = 0;
        int newsBudget = (int) (MAX_INPUT_TOKENS * NEWS_RATIO);
        int postBudget = (int) (MAX_INPUT_TOKENS * POST_RATIO);
        int commentBudget = (int) (MAX_INPUT_TOKENS * COMMENT_RATIO);

        // 新闻段
        if (!news.isEmpty()) {
            sb.append("=== 新闻文章 ===\n");
            int tokens = 0;
            for (Map<String, Object> n : news) {
                String item = formatNews(n);
                int t = estimateTokens(item);
                if (tokens + t > newsBudget) break;
                sb.append(item).append("\n");
                tokens += t;
            }
            totalTokens += tokens;
        }

        // 帖子段
        if (!posts.isEmpty()) {
            sb.append("\n=== 社交帖子 ===\n");
            int tokens = 0;
            for (Map<String, Object> p : posts) {
                String item = formatPost(p);
                int t = estimateTokens(item);
                if (tokens + t > postBudget) break;
                sb.append(item).append("\n");
                tokens += t;
            }
            totalTokens += tokens;
        }

        // 评论段
        if (!comments.isEmpty()) {
            sb.append("\n=== 热门评论 ===\n");
            int tokens = 0;
            for (Map<String, Object> c : comments) {
                String item = formatComment(c);
                int t = estimateTokens(item);
                if (tokens + t > commentBudget) break;
                sb.append(item).append("\n");
                tokens += t;
            }
            totalTokens += tokens;
        }

        // 前次摘要段
        if (prev != null) {
            int prevBudget = (int) (MAX_INPUT_TOKENS * PREV_RATIO);
            String prevText = formatPrevSummary(prev);
            int t = estimateTokens(prevText);
            if (t <= prevBudget) {
                sb.append("\n").append(prevText);
            }
        }

        return sb.toString();
    }

    private String formatNews(Map<String, Object> n) {
        String title = str(n.get("title"));
        String source = str(n.get("source"));
        String keywords = str(n.get("keywords"));
        String content = truncate(str(n.get("content")), MAX_CONTENT_LEN);
        StringBuilder sb = new StringBuilder();
        sb.append("【").append(source).append("】").append(title);
        if (!keywords.isEmpty()) sb.append("\n  关键词: ").append(keywords);
        if (!content.isEmpty() && !content.equals(title)) sb.append("\n  ").append(content);
        return sb.toString();
    }

    private String formatPost(Map<String, Object> p) {
        String title = truncate(str(p.get("title")), 100);
        String author = str(p.get("author"));
        String site = str(p.get("site_name"));
        int likes = intVal(p.get("like_count"));
        int comments = intVal(p.get("comment_count"));
        String keyword = str(p.get("trigger_keyword"));
        String content = truncate(str(p.get("content")), 150);
        StringBuilder sb = new StringBuilder();
        sb.append("[").append(site).append("] @").append(author).append(": ").append(title);
        sb.append("\n  互动: ").append(likes).append("赞 ").append(comments).append("评 | 关键词: ").append(keyword);
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("\n  内容: ").append(content);
        }
        return sb.toString();
    }

    private String formatComment(Map<String, Object> c) {
        String commenter = str(c.get("commenter"));
        String content = truncate(str(c.get("comment_content")), 150);
        int likes = intVal(c.get("comment_likes"));
        String postTitle = truncate(str(c.get("post_title")), 60);
        String site = str(c.get("site_name"));
        return "[" + site + "] " + commenter + " (👍" + likes + ")\n" +
               "  评论: " + content + "\n" +
               "  原帖: " + postTitle;
    }

    private String formatPrevSummary(Map<String, Object> prev) {
        String title = str(prev.get("title"));
        String risk = str(prev.get("risk_level"));
        String time = str(prev.get("create_time"));
        int newsCount = intVal(prev.get("news_count"));
        int socialCount = intVal(prev.get("social_count"));
        String content = str(prev.get("content"));

        // 提取核心摘要部分
        String summaryText = extractCoreSummary(content);

        return "=== 上次简报 (时间: " + time + ") ===\n" +
               "标题: " + title + "\n" +
               "风险等级: " + risk + "\n" +
               "数据量: " + newsCount + "条新闻 + " + socialCount + "条社交\n" +
               "核心摘要: " + truncate(summaryText, 500);
    }

    private String extractCoreSummary(String content) {
        if (content == null || content.isEmpty()) return "";
        // 查找 "## 2" 或 "核心摘要" 之后的内容，到 "## 3" 之前
        String[] lines = content.split("\\n");
        boolean inSummary = false;
        StringBuilder sb = new StringBuilder();
        for (String line : lines) {
            if (line.contains("## 2") || line.contains("核心摘要")) {
                inSummary = true;
                continue;
            }
            if (inSummary && (line.startsWith("## 3") || line.startsWith("## "))) break;
            if (inSummary) sb.append(line).append("\n");
        }
        String result = sb.toString().trim();
        return result.isEmpty() ? truncate(content, 500) : result;
    }

    // ==================== Prompt 构建 ====================

    private String buildPrompt(int hours, int newsCount, int postCount, int commentCount,
                                String prevSection, String dataSection) {
        return "请根据以下过去 " + hours + " 小时内抓取的舆情数据，生成一份专业的舆情监测简报。\n\n" +
               "数据概览：" + newsCount + " 条新闻，" + postCount + " 条社交帖子，" + commentCount + " 条热门评论。\n" +
               prevSection + "\n" +
               "数据内容：\n" + dataSection + "\n\n" +
               "请严格按照以下格式输出 Markdown 简报：\n\n" +
               "## 1. 标题\n用一句话概括本时段舆情态势\n\n" +
               "## 2. 核心摘要\n3-5 句话总结最重要的事件和趋势\n\n" +
               "## 3. 分类统计\n按主题分类（军事、贸易、人权、外交、科技、社会等），每个分类列出关键事件，格式：\n" +
               "### 分类名\n- 事件1（来源）\n- 事件2（来源）\n\n" +
               "## 4. 热门互动分析\n分析本时段内点赞/评论互动最高的帖子和评论，总结舆论焦点\n\n" +
               "## 5. 舆情变化对比\n与上一次简报对比，分析风险趋势变化（升高/持平/下降），新增的重要议题\n\n" +
               "## 6. 风险评级\n**评级：低/中/高**\n\n说明理由\n\n" +
               "## 7. 关注建议\n下一步需要重点关注的方向（3-5 条）\n\n" +
               "请用中文输出。";
    }

    // ==================== Ollama 调用 ====================

    private boolean checkOllama() {
        try {
            HttpClient client = HttpClient.newHttpClient();
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/tags"))
                .timeout(java.time.Duration.ofSeconds(10))
                .GET().build();
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            return resp.statusCode() == 200;
        } catch (Exception e) {
            log.warn("[AI Summary] Ollama 连接失败: {}", e.getMessage());
            return false;
        }
    }

    private String callOllama(String prompt) {
        try {
            Map<String, Object> body = Map.of(
                "model", MODEL_NAME,
                "messages", List.of(
                    Map.of("role", "system", "content", "你是一个专业的舆情分析师，服务于一个舆情监测平台。你的任务是根据提供的实时抓取数据，生成一份专业的舆情简报。"),
                    Map.of("role", "user", "content", prompt)
                ),
                "stream", false
            );
            String json = mapper.writeValueAsString(body);

            HttpClient client = HttpClient.newHttpClient();
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/chat"))
                .timeout(java.time.Duration.ofSeconds(300))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();

            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 200) {
                JsonNode node = mapper.readTree(resp.body());
                return node.path("message").path("content").asText("");
            }
            log.warn("[AI Summary] Ollama 返回: {}", resp.statusCode());
        } catch (Exception e) {
            log.error("[AI Summary] Ollama 调用异常: {}", e.getMessage());
        }
        return null;
    }

    // ==================== 解析 & 存储 ====================

    private String extractTitle(String content) {
        String[] lines = content.split("\\n");
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].contains("## 1") || lines[i].contains("标题")) {
                for (int j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                    String candidate = lines[j].trim();
                    if (!candidate.isEmpty() && !candidate.startsWith("#")) {
                        return candidate.replaceAll("[*#]", "").trim();
                    }
                }
            }
        }
        return "舆情简报 " + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"));
    }

    private String extractRisk(String content) {
        String[] lines = content.split("\\n");
        for (String line : lines) {
            if ((line.contains("评级") || line.contains("风险")) && line.startsWith("#")) {
                String upper = line.toUpperCase();
                if (upper.contains("高") || upper.contains("HIGH")) return "高";
                if (upper.contains("低") || upper.contains("LOW")) return "低";
                return "中";
            }
        }
        return "中";
    }

    private void saveSummary(String title, String content, String riskLevel,
                              LocalDateTime dataStart, LocalDateTime dataEnd,
                              int newsCount, int socialCount, int genSeconds) {
        jdbc.update(
            "INSERT INTO ai_summary (summary_type, title, content, risk_level, " +
            "data_start, data_end, news_count, social_count, model_name, generate_time) " +
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "hourly", title, content, riskLevel,
            java.sql.Timestamp.valueOf(dataStart), java.sql.Timestamp.valueOf(dataEnd),
            newsCount, socialCount, MODEL_NAME, genSeconds
        );
    }


    private String buildPrevSection(Map<String, Object> prev) {
        if (prev == null) return "";
        String title = str(prev.get("title"));
        String risk = str(prev.get("risk_level"));
        String time = str(prev.get("create_time"));
        return "\n上次简报 (" + time + "): " + title + " [风险: " + risk + "]";
    }

    // ==================== 工具方法 ====================

    private int estimateTokens(String text) {
        if (text == null) return 0;
        return (int) (text.length() * TOKEN_PER_CHAR);
    }

    private String truncate(String s, int maxLen) {
        if (s == null) return "";
        s = s.trim();
        return s.length() <= maxLen ? s : s.substring(0, maxLen) + "...";
    }

    private String str(Object o) {
        return o == null ? "" : o.toString();
    }

    private int intVal(Object o) {
        if (o == null) return 0;
        if (o instanceof Number) return ((Number) o).intValue();
        try { return Integer.parseInt(o.toString()); } catch (Exception e) { return 0; }
    }

    private int countSection(String data, String sectionName) {
        String[] lines = data.split("\\n");
        boolean inSection = false;
        int count = 0;
        for (String line : lines) {
            if (line.contains(sectionName)) { inSection = true; continue; }
            if (inSection && line.startsWith("===")) break;
            if (inSection && !line.trim().isEmpty() && !line.startsWith("===")) count++;
        }
        return count;
    }
}
