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
 * AI Summary Generator - V2
 * Layered data fetch + pre-summarization + structured prompt for Ollama.
 */
@Service
public class AiSummaryGenerator {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryGenerator.class);

    private static final String OLLAMA_BASE = "http://200m.frpee.com:18138";
    private static final String MODEL_NAME = "qwen3.6:latest";

    // Token estimation: Chinese ~1.5 tokens/char
    private static final double TOKEN_PER_CHAR = 1.5;
    private static final int MAX_INPUT_TOKENS = 28000;

    // Safe budget: 90% of max input
    private static final int SAFE_TOKENS = (int) (MAX_INPUT_TOKENS * 0.9); // 28800

    // Budget allocation
    private static final int NEWS_BUDGET = (int) (SAFE_TOKENS * 0.40);   // 11520
    private static final int POST_BUDGET = (int) (SAFE_TOKENS * 0.50);   // 14400
    private static final int PREV_BUDGET = (int) (SAFE_TOKENS * 0.10);   // 2880

    // Pre-summarization thresholds
    private static final int NEWS_SUMMARY_THRESHOLD = 1500;
    private static final int POST_SUMMARY_THRESHOLD = 800;
    private static final int MAX_PRE_SUMMARIZE_CALLS = 10;  // Max Ollama pre-summary calls

    // Limits
    private static final int MAX_POSTS = 100;
    private static final int MAX_NEWS = 80;
    private static final int TOP_COMMENTS_PER_POST = 20;

    // Time windows
    private static final int POST_FRESH_HOURS = 1;
    private static final int NEWS_FRESH_HOURS = 12;

    @Autowired
    private JdbcTemplate jdbc;

    private final ObjectMapper mapper = new ObjectMapper();

    /**
     * Main entry point - generates a complete summary report.
     * Public interface unchanged for AiSummaryScheduler compatibility.
     * @param hours nominal reporting window (not strictly used for data fetch anymore)
     * @return true on success, false on failure/skip
     */
    public boolean generate(int hours) {
        log.info("[AI Summary] === Starting report generation ===");

        if (!checkOllama()) {
            log.warn("[AI Summary] Ollama unavailable, skipping");
            return false;
        }

        LocalDateTime now = LocalDateTime.now();
        try {

        // ---- Layered data fetch ----
        List<Map<String, Object>> posts = fetchPostsLayered(now);
        List<Map<String, Object>> news = fetchNewsLayered(now);
        log.info("[AI Summary] Posts: {}, News: {}", posts.size(), news.size());

        // Fetch comments associated with fetched posts
        List<String> postIds = extractPostIds(posts);
        List<Map<String, Object>> comments = postIds.isEmpty()
            ? Collections.emptyList()
            : fetchPostComments(postIds);
        log.info("[AI Summary] Comments: {} (for {} posts)", comments.size(), postIds.size());

        Map<String, Object> prevSummary = fetchPreviousSummary();

        // ---- Freshness check ----
        boolean hasFreshPosts = hasFreshPosts(posts);
        boolean hasFreshNews = hasFreshNews(news);
        log.info("[AI Summary] Fresh posts: {}, Fresh news: {}", hasFreshPosts, hasFreshNews);

        if (!hasFreshPosts && !hasFreshNews) {
            // No new data - skip generation but record it
            log.info("[AI Summary] No fresh data, skipping generation");
            saveSkippedSummary(now, posts.size(), news.size());
            return true;
        }

        // ---- Pre-summarize long content via Ollama ----
        preSummarizeLongContent(posts, "post");
        preSummarizeLongContent(news, "news");

        // ---- Build prompt ----
        String dataSection = buildDataSection(posts, comments, news);
        int newsCount = news.size();
        int postCount = posts.size();
        int commentCount = comments.size();

        String prompt = buildPrompt(newsCount, postCount, commentCount, prevSummary, dataSection);
        int promptTokens = estimateTokens(prompt);
        log.info("[AI Summary] Prompt estimated tokens: {} / {} (safe budget)", promptTokens, SAFE_TOKENS);

        // ---- Call Ollama ----
        long startMs = System.currentTimeMillis();
        String content = callOllama(prompt);
        if (content == null) {
            log.warn("[AI Summary] Ollama call failed");
            return false;
        }
        int genSeconds = (int) ((System.currentTimeMillis() - startMs) / 1000);
        log.info("[AI Summary] Generation complete ({}s, {} chars)", genSeconds, content.length());

        // ---- Parse and save ----
        String title = extractTitle(content);
        String riskLevel = extractRisk(content);
        log.info("[AI Summary] Title: {}", title);
        log.info("[AI Summary] Risk: {}", riskLevel);

        saveSummary(title, content, riskLevel, now, newsCount, postCount, genSeconds);
        log.info("[AI Summary] === Done ===");
        return true;
        } catch (Exception e) {
            log.error("[AI Summary] Generation failed with exception: {}", e.getMessage());
            try {
                saveSkippedSummary(now, 0, 0);
            } catch (Exception ignored) {}
            return false;
        }
    }

    // ==================== Data Fetching ====================

    /**
     * Layer 1: posts within 1 hour. Layer 2: today's posts if layer 1 insufficient.
     * Sorted by crawl_time DESC.
     */
    private List<Map<String, Object>> fetchPostsLayered(LocalDateTime now) {
        LocalDateTime oneHourAgo = now.minusHours(POST_FRESH_HOURS);
        String sql1h = "SELECT post_id, title, author, site_name, like_count, comment_count, "
            + "content, trigger_keyword, crawl_time FROM social_post "
            + "WHERE crawl_time > ? ORDER BY crawl_time DESC LIMIT ?";
        List<Map<String, Object>> layer1 = jdbc.queryForList(sql1h,
            java.sql.Timestamp.valueOf(oneHourAgo), MAX_POSTS);
        log.info("[AI Summary] Posts layer1 (1h): {}", layer1.size());

        if (!layer1.isEmpty()) {
            return layer1;
        }

        // Layer 2: today
        LocalDateTime todayStart = now.toLocalDate().atStartOfDay();
        List<Map<String, Object>> layer2 = jdbc.queryForList(sql1h,
            java.sql.Timestamp.valueOf(todayStart), MAX_POSTS);
        log.info("[AI Summary] Posts layer2 (today): {}", layer2.size());
        return layer2;
    }

    /**
     * Layer 1: news within 12 hours. Layer 2: today's news if layer 1 insufficient.
     * Sorted by crawl_time DESC.
     */
    private List<Map<String, Object>> fetchNewsLayered(LocalDateTime now) {
        LocalDateTime twelveHoursAgo = now.minusHours(NEWS_FRESH_HOURS);
        String sql12h = "SELECT title, source, keywords, content, publish_date, crawl_time "
            + "FROM news_article WHERE crawl_time > ? ORDER BY crawl_time DESC LIMIT ?";
        List<Map<String, Object>> layer1 = jdbc.queryForList(sql12h,
            java.sql.Timestamp.valueOf(twelveHoursAgo), MAX_NEWS);
        log.info("[AI Summary] News layer1 (12h): {}", layer1.size());

        if (!layer1.isEmpty()) {
            return layer1;
        }

        // Layer 2: today
        LocalDateTime todayStart = now.toLocalDate().atStartOfDay();
        List<Map<String, Object>> layer2 = jdbc.queryForList(sql12h,
            java.sql.Timestamp.valueOf(todayStart), MAX_NEWS);
        log.info("[AI Summary] News layer2 (today): {}", layer2.size());
        return layer2;
    }

    /**
     * Fetch comments for given post IDs, TOP 20 per post by like_count DESC.
     */
    private List<Map<String, Object>> fetchPostComments(List<String> postIds) {
        if (postIds.isEmpty()) {
            return Collections.emptyList();
        }

        // Build IN clause safely
        String placeholders = String.join(",", Collections.nCopies(postIds.size(), "?"));
        String sql = "SELECT sc.post_id, sc.commenter, sc.comment_content, "
            + "sc.like_count AS comment_likes, "
            + "sp.title AS post_title, sp.author AS post_author, sp.site_name "
            + "FROM social_comment sc JOIN social_post sp ON sc.post_id = sp.post_id "
            + "WHERE sc.post_id IN (" + placeholders + ") "
            + "ORDER BY sc.like_count DESC";

        List<Map<String, Object>> allComments = jdbc.queryForList(sql, postIds.toArray());

        // Group by post_id and take TOP N per post
        Map<String, List<Map<String, Object>>> grouped = new LinkedHashMap<>();
        for (Map<String, Object> c : allComments) {
            String pid = str(c.get("post_id"));
            grouped.computeIfAbsent(pid, k -> new ArrayList<>()).add(c);
        }

        List<Map<String, Object>> topComments = new ArrayList<>();
        for (List<Map<String, Object>> perPost : grouped.values()) {
            int limit = Math.min(TOP_COMMENTS_PER_POST, perPost.size());
            topComments.addAll(perPost.subList(0, limit));
        }
        return topComments;
    }

    /**
     * Fetch the previous complete report for trend comparison.
     */
    private Map<String, Object> fetchPreviousSummary() {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT title, risk_level, content, news_count, social_count, "
            + "DATE_FORMAT(create_time, '%m-%d %H:%i') AS create_time "
            + "FROM ai_summary WHERE summary_type != 'skipped' "
            + "ORDER BY id DESC LIMIT 1"
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ==================== Skip Logic ====================

    private boolean hasFreshPosts(List<Map<String, Object>> posts) {
        if (posts.isEmpty()) return false;
        // If all posts are older than 1 hour, they are not fresh
        LocalDateTime cutoff = LocalDateTime.now().minusHours(POST_FRESH_HOURS);
        for (Map<String, Object> p : posts) {
            Object ct = p.get("crawl_time");
            if (ct != null) {
                LocalDateTime crawlTime = parseDateTime(ct);
                if (crawlTime != null && crawlTime.isAfter(cutoff)) {
                    return true;
                }
            }
        }
        return false;
    }

    private boolean hasFreshNews(List<Map<String, Object>> news) {
        if (news.isEmpty()) return false;
        LocalDateTime cutoff = LocalDateTime.now().minusHours(NEWS_FRESH_HOURS);
        for (Map<String, Object> n : news) {
            Object ct = n.get("crawl_time");
            if (ct != null) {
                LocalDateTime crawlTime = parseDateTime(ct);
                if (crawlTime != null && crawlTime.isAfter(cutoff)) {
                    return true;
                }
            }
        }
        return false;
    }

    private void saveSkippedSummary(LocalDateTime now, int postCount, int newsCount) {
        String content = "Skipped. Posts: " + postCount + " (none fresh), "
            + "News: " + newsCount + " (none fresh). "
            + "No new data within time windows. Time: "
            + now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"));
        jdbc.update(
            "INSERT INTO ai_summary (summary_type, title, content, risk_level, "
            + "data_start, data_end, news_count, social_count, model_name, generate_time) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "skipped", "Skipped - insufficient fresh data", content, "low",
            java.sql.Timestamp.valueOf(now), java.sql.Timestamp.valueOf(now),
            newsCount, postCount, MODEL_NAME, 0
        );
    }

    // ==================== Pre-summarization ====================

    /**
     * For posts/news whose content exceeds the token threshold,
     * call Ollama to generate a compact pre-summary.
     * Replaces the content field with the pre-summary (title always kept intact).
     */
    private void preSummarizeLongContent(List<Map<String, Object>> items, String type) {
        int callCount = 0;
        int maxCalls = "news".equals(type) ? MAX_PRE_SUMMARIZE_CALLS : MAX_PRE_SUMMARIZE_CALLS;
        for (Map<String, Object> item : items) {
            String title = str(item.get("title"));
            String content = str(item.get("content"));
            if (content.isEmpty() || content.equals(title)) continue;

            int threshold = "news".equals(type) ? NEWS_SUMMARY_THRESHOLD : POST_SUMMARY_THRESHOLD;
            int tokens = estimateTokens(content);

            if (tokens > threshold) {
                if (callCount < maxCalls) {
                    log.info("[AI Summary] Pre-summarizing {}/{} {} ({} tokens): {}",
                        callCount + 1, maxCalls, type, tokens, truncate(title, 60));
                    String summary = callPreSummarize(title, content, type);
                    if (summary != null && !summary.isEmpty()) {
                        item.put("_pre_summary", summary);
                    }
                    callCount++;
                } else {
                    // Fallback: truncate to 500 chars
                    item.put("_pre_summary", truncate(content, 500));
                    log.info("[AI Summary] Truncating {} ({} tokens, pre-summary limit reached): {}",
                        type, tokens, truncate(title, 60));
                }
            }
        }
    }

    private String callPreSummarize(String title, String content, String type) {
        // Limit pre-summary input to avoid Ollama overload
        String truncatedContent = truncate(content, 2000);
        String prompt;
        if ("news".equals(type)) {
            prompt = "\u8bf7\u4e3a\u4ee5\u4e0b\u65b0\u95fb\u751f\u6210150\u5b57\u4ee5\u5185\u7684\u6838\u5fc3\u6458\u8981\uff0c\u4fdd\u7559\u5173\u952e\u4fe1\u606f\uff08\u65f6\u95f4\u3001\u5730\u70b9\u3001\u4eba\u7269\u3001\u4e8b\u4ef6\u3001\u5f71\u54cd\uff09\uff1a\u6807\u9898\uff1a"
                + title + " \u6b63\u6587\uff1a" + truncatedContent;
        } else {
            prompt = "\u8bf7\u4e3a\u4ee5\u4e0b\u5e16\u5b50\u751f\u6210100\u5b57\u4ee5\u5185\u7684\u6838\u5fc3\u6458\u8981\uff0c\u4fdd\u7559\u5173\u952e\u4fe1\u606f\uff08\u8bdd\u9898\u3001\u4f5c\u8005\u89c2\u70b9\u3001\u5173\u952e\u7ec6\u8282\uff09\uff1a\u6807\u9898\uff1a"
                + title + " \u6b63\u6587\uff1a" + truncatedContent;
        }

        try {
            Map<String, Object> body = Map.of(
                "model", MODEL_NAME,
                "messages", List.of(
                    Map.of("role", "system", "content",
                        "\u4f60\u662f\u4e00\u4e2a\u6587\u672c\u6458\u8981\u52a9\u624b\u3002\u8bf7\u7528\u7b80\u6d01\u7684\u4e2d\u6587\u6982\u62ec\u4ee5\u4e0b\u5185\u5bb9\u7684\u6838\u5fc3\u8981\u70b9\u3002"),
                    Map.of("role", "user", "content", prompt)
                ),
                "stream", false
            );
            String json = mapper.writeValueAsString(body);

            HttpClient client = HttpClient.newHttpClient();
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/chat"))
                .timeout(java.time.Duration.ofSeconds(120))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();

            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() == 200) {
                JsonNode node = mapper.readTree(resp.body());
                return node.path("message").path("content").asText("");
            }
        } catch (Exception e) {
            log.warn("[AI Summary] Pre-summarize failed: {}", e.getMessage());
        }
        return null;
    }

    // ==================== Data Formatting ====================

    /**
     * Build the complete data section for the prompt.
     * Includes posts with embedded comments, and news.
     * Respects token budgets.
     */
    private String buildDataSection(List<Map<String, Object>> posts,
                                     List<Map<String, Object>> comments,
                                     List<Map<String, Object>> news) {
        StringBuilder sb = new StringBuilder();
        int usedTokens = 0;

        // Group comments by post_id for easy lookup
        Map<String, List<Map<String, Object>>> commentsByPost = new LinkedHashMap<>();
        for (Map<String, Object> c : comments) {
            String pid = str(c.get("post_id"));
            commentsByPost.computeIfAbsent(pid, k -> new ArrayList<>()).add(c);
        }

        // ---- Posts + Comments section ----
        if (!posts.isEmpty()) {
            sb.append("=== \u793e\u4ea4\u5e16\u5b50\u4e0e\u8bc4\u8bba ===\n");
            int postTokens = 0;
            for (Map<String, Object> p : posts) {
                String pid = str(p.get("post_id"));
                List<Map<String, Object>> postComments = commentsByPost.getOrDefault(pid, Collections.emptyList());
                String block = buildPostBlock(p, postComments);
                int blockTokens = estimateTokens(block);
                if (postTokens + blockTokens > POST_BUDGET) break;
                sb.append(block).append("\n");
                postTokens += blockTokens;
            }
            usedTokens += postTokens;
        }

        // ---- News section ----
        if (!news.isEmpty()) {
            sb.append("\n=== \u65b0\u95fb\u6587\u7ae0 ===\n");
            int newsTokens = 0;
            for (Map<String, Object> n : news) {
                String item = formatNews(n);
                int t = estimateTokens(item);
                if (newsTokens + t > NEWS_BUDGET) break;
                sb.append(item).append("\n");
                newsTokens += t;
            }
            usedTokens += newsTokens;
        }

        return sb.toString();
    }

    /**
     * Build a single post block with its associated comments.
     */
    private String buildPostBlock(Map<String, Object> post, List<Map<String, Object>> comments) {
        String site = str(post.get("site_name"));
        String author = str(post.get("author"));
        String title = str(post.get("title"));
        int likes = intVal(post.get("like_count"));
        int commentCount = intVal(post.get("comment_count"));
        String keyword = str(post.get("trigger_keyword"));

        // Content: use pre-summary if available, otherwise full content
        String content;
        Object preSummary = post.get("_pre_summary");
        if (preSummary != null) {
            content = str(preSummary);
        } else {
            content = str(post.get("content"));
        }

        StringBuilder sb = new StringBuilder();
        sb.append("[").append(site).append("] @").append(author).append(": ").append(title).append("\n");
        sb.append("\u4e92\u52a8: ").append(likes).append("\u8d5e ").append(commentCount)
          .append("\u8bc4 | \u5173\u952e\u8bcd: ").append(keyword).append("\n");
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("\u5185\u5bb9: ").append(content).append("\n");
        }

        // Add top comments
        if (!comments.isEmpty()) {
            sb.append("  \u70ed\u95e8\u8bc4\u8bba:\n");
            int idx = 1;
            for (Map<String, Object> c : comments) {
                String commenter = str(c.get("commenter"));
                String commentContent = str(c.get("comment_content"));
                int cLikes = intVal(c.get("comment_likes"));
                sb.append("  ").append(idx++).append(". ").append(commenter)
                  .append(" (\ud83d\udc4d").append(cLikes).append("): ").append(commentContent).append("\n");
            }
        }

        return sb.toString();
    }

    /**
     * Format a single news item. Title always kept intact. Content uses pre-summary if available.
     */
    private String formatNews(Map<String, Object> n) {
        String title = str(n.get("title"));
        String source = str(n.get("source"));
        String keywords = str(n.get("keywords"));

        // Content: use pre-summary if available, otherwise full content
        String content;
        Object preSummary = n.get("_pre_summary");
        if (preSummary != null) {
            content = str(preSummary);
        } else {
            content = str(n.get("content"));
        }

        StringBuilder sb = new StringBuilder();
        sb.append("[").append(source).append("] ").append(title).append("\n");
        if (!keywords.isEmpty()) {
            sb.append("\u5173\u952e\u8bcd: ").append(keywords).append("\n");
        }
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("\u6b63\u6587: ").append(content).append("\n");
        }
        return sb.toString();
    }

    /**
     * Build the previous summary section for trend comparison.
     * Extracts core summary from the previous report content.
     */
    private String buildPrevSection(Map<String, Object> prev) {
        if (prev == null) return "";
        String title = str(prev.get("title"));
        String risk = str(prev.get("risk_level"));
        String time = str(prev.get("create_time"));
        int newsCount = intVal(prev.get("news_count"));
        int socialCount = intVal(prev.get("social_count"));
        String content = str(prev.get("content"));

        // Extract core summary part
        String summaryText = extractCoreSummary(content);

        return "\n=== PREVIOUS REPORT (Time: " + time + ") ===\n"
            + "Title: " + title + "\n"
            + "Risk Level: " + risk + "\n"
            + "Data Volume: " + newsCount + " news + " + socialCount + " social posts\n"
            + "Core Summary: " + truncate(summaryText, 500);
    }

    private String extractCoreSummary(String content) {
        if (content == null || content.isEmpty()) return "";
        // Find content after "## 2" or "Core Summary" up to "## 3"
        String[] lines = content.split("\n");
        boolean inSummary = false;
        StringBuilder sb = new StringBuilder();
        for (String line : lines) {
            if (line.contains("## 2") || line.contains("Core Summary") || line.contains("\u6838\u5fc3\u6458\u8981")) {
                inSummary = true;
                continue;
            }
            if (inSummary && (line.startsWith("## 3") || line.startsWith("## "))) break;
            if (inSummary) sb.append(line).append("\n");
        }
        String result = sb.toString().trim();
        return result.isEmpty() ? truncate(content, 500) : result;
    }

    // ==================== Prompt Construction ====================

    private String buildPrompt(int newsCount, int postCount, int commentCount,
                               Map<String, Object> prevSummary, String dataSection) {
        StringBuilder sb = new StringBuilder();

        sb.append("\u8bf7\u6839\u636e\u4ee5\u4e0b\u8206\u60c5\u6570\u636e\uff0c\u751f\u6210\u4e00\u4efd\u4e13\u4e1a\u7684\u8206\u60c5\u76d1\u6d4b\u7b80\u62a5\u3002\n\n");

        sb.append("\u6570\u636e\u6982\u89c8\uff1a").append(newsCount).append("\u6761\u65b0\u95fb\uff0c")
          .append(postCount).append("\u6761\u793e\u4ea4\u5e16\u5b50\u3002\n");

        // Previous report for trend comparison
        String prevSection = buildPrevSection(prevSummary);
        if (!prevSection.isEmpty()) {
            sb.append(prevSection).append("\n");
        }

        sb.append("\n\u6570\u636e\u5185\u5bb9\uff1a\n");
        sb.append(dataSection).append("\n\n");

        sb.append("\u8bf7\u4e25\u683c\u6309\u7167\u4ee5\u4e0b\u683c\u5f0f\u8f93\u51fa Markdown \u7b80\u62a5\uff1a\n\n");
        sb.append("## 1. \u6807\u9898\n\u7528\u4e00\u53e5\u8bdd\u6982\u62ec\u672c\u65f6\u6bb5\u8206\u60c5\u6001\u52bf\n\n");
        sb.append("## 2. \u6838\u5fc3\u6458\u8981\n3-5\u53e5\u8bdd\u603b\u7ed3\u6700\u91cd\u8981\u7684\u4e8b\u4ef6\u548c\u8d8b\u52bf\n\n");
        sb.append("## 3. \u5206\u7c7b\u7edf\u8ba1\n\u6309\u4e3b\u9898\u5206\u7c7b\uff08\u519b\u4e8b\u3001\u8d38\u6613\u3001\u4eba\u6743\u3001\u5916\u4ea4\u3001\u79d1\u6280\u3001\u793e\u4f1a\u7b49\uff09\uff0c\u6bcf\u4e2a\u5206\u7c7b\u5217\u51fa\u5173\u952e\u4e8b\u4ef6\n\n");
        sb.append("## 4. \u70ed\u95e8\u4e92\u52a8\u5206\u6790\n\u5206\u6790\u70b9\u8d5e/\u8bc4\u8bba\u4e92\u52a8\u6700\u9ad8\u7684\u5e16\u5b50\u548c\u8bc4\u8bba\uff0c\u603b\u7ed3\u8206\u8bba\u7126\u70b9\n\n");
        sb.append("## 5. \u8206\u60c5\u53d8\u5316\u5bf9\u6bd4\n\u4e0e\u4e0a\u4e00\u6b21\u7b80\u62a5\u5bf9\u6bd4\uff0c\u5206\u6790\u98ce\u9669\u8d8b\u52bf\u53d8\u5316\uff08\u5347\u9ad8/\u6301\u5e73/\u4e0b\u964d\uff09\uff0c\u65b0\u589e\u7684\u91cd\u8981\u8bae\u9898\n\n");
        sb.append("## 6. \u98ce\u9669\u8bc4\u7ea7\n\u8bc4\u7ea7\uff1a\u4f4e/\u4e2d/\u9ad8\n\n\u8bf4\u660e\u7406\u7531\n\n");
        sb.append("## 7. \u5173\u6ce8\u5efa\u8bae\n\u4e0b\u4e00\u6b65\u9700\u8981\u91cd\u70b9\u5173\u6ce8\u7684\u65b9\u5411\uff083-5\u6761\uff09\n\n");
        sb.append("\u8bf7\u7528\u4e2d\u6587\u8f93\u51fa\u3002");

        return sb.toString();
    }

    // ==================== Ollama Calls ====================

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
            log.warn("[AI Summary] Ollama connection failed: {}", e.getMessage());
            return false;
        }
    }

    private String callOllama(String prompt) {
        try {
            Map<String, Object> body = Map.of(
                "model", MODEL_NAME,
                "messages", List.of(
                    Map.of("role", "system", "content",
                        "\u4f60\u662f\u4e00\u4e2a\u4e13\u4e1a\u7684\u8206\u60c5\u5206\u6790\u5e08\uff0c\u670d\u52a1\u4e8e\u4e00\u4e2a\u8206\u60c5\u76d1\u6d4b\u5e73\u53f0\u3002"),
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
            log.warn("[AI Summary] Ollama returned status: {}", resp.statusCode());
        } catch (Exception e) {
            log.error("[AI Summary] Ollama call exception: {}", e.getMessage());
        }
        return null;
    }

    // ==================== Parsing & Storage ====================

    private String extractTitle(String content) {
        String[] lines = content.split("\n");
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].contains("## 1") || lines[i].contains("Title") || lines[i].contains("\u6807\u9898")) {
                for (int j = i + 1; j < Math.min(i + 5, lines.length); j++) {
                    String candidate = lines[j].trim();
                    if (!candidate.isEmpty() && !candidate.startsWith("#")) {
                        return candidate.replaceAll("[*#]", "").trim();
                    }
                }
            }
        }
        return "Sentiment Report " + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm"));
    }

    private String extractRisk(String content) {
        String[] lines = content.split("\n");
        for (String line : lines) {
            if ((line.contains("Rating") || line.contains("Risk") || line.contains("\u8bc4\u7ea7") || line.contains("\u98ce\u9669"))
                && line.startsWith("#")) {
                String upper = line.toUpperCase();
                if (upper.contains("HIGH") || upper.contains("\u9ad8")) return "\u9ad8";
                if (upper.contains("LOW") || upper.contains("\u4f4e")) return "\u4f4e";
                return "\u4e2d";
            }
            // Also check for bold rating on its own line
            if (line.contains("Rating") || line.contains("\u8bc4\u7ea7")) {
                String upper = line.toUpperCase();
                if (upper.contains("HIGH") || upper.contains("\u9ad8")) return "\u9ad8";
                if (upper.contains("LOW") || upper.contains("\u4f4e")) return "\u4f4e";
                if (upper.contains("MEDIUM") || upper.contains("\u4e2d")) return "\u4e2d";
            }
        }
        return "\u4e2d";
    }

    private void saveSummary(String title, String content, String riskLevel,
                              LocalDateTime dataEnd, int newsCount, int socialCount, int genSeconds) {
        jdbc.update(
            "INSERT INTO ai_summary (summary_type, title, content, risk_level, "
            + "data_start, data_end, news_count, social_count, model_name, generate_time) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "hourly", title, content, riskLevel,
            java.sql.Timestamp.valueOf(dataEnd), java.sql.Timestamp.valueOf(dataEnd),
            newsCount, socialCount, MODEL_NAME, genSeconds
        );
    }

    // ==================== Utility Methods ====================

    private List<String> extractPostIds(List<Map<String, Object>> posts) {
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> p : posts) {
            Object pid = p.get("post_id");
            if (pid != null) {
                ids.add(pid.toString());
            }
        }
        return ids;
    }

    private LocalDateTime parseDateTime(Object dt) {
        if (dt == null) return null;
        try {
            if (dt instanceof java.sql.Timestamp) {
                return ((java.sql.Timestamp) dt).toLocalDateTime();
            }
            if (dt instanceof LocalDateTime) {
                return (LocalDateTime) dt;
            }
            String s = dt.toString();
            // Try common formats
            return LocalDateTime.parse(s, DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        } catch (Exception e) {
            try {
                return LocalDateTime.parse(dt.toString().substring(0, 19),
                    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
            } catch (Exception ex) {
                return null;
            }
        }
    }

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
}
