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

import java.util.Collections;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * AI Summary Generator - V5 (Structured JSON Output)
 * Fixed-window data fetch (6h for posts, 24h for news)
 * + dual-model: pre-summarization (qwen3.5 4b) + generation (qwen3.6 27B)
 * + Output: structured JSON with title, summary, categories, stats, trends, suggestions
 * + 27B model only generates short summary (3-5 sentences) + risk assessment
 * + Statistical data extracted by Java code from pre-summarized results
 * + keep_alive=-1: both models stay resident in memory (no swap)
 */
@Service
public class AiSummaryGenerator {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryGenerator.class);

    private static final String OLLAMA_BASE = "http://200m.frpee.com:18138";
    private static final String PRE_SUMMARY_MODEL = "qwen3.5:4b-q4_K_M";  // 4B: fully in VRAM (~3.4GB)
    private static final String GENERATION_MODEL = "qwen3.6:27b-partial";  // 27B: 45/64 layers GPU + KV cache


    // Token estimation: Chinese ~1.5 tokens/char
    private static final double TOKEN_PER_CHAR = 1.5;
    private static final int MAX_INPUT_TOKENS = 32000;

    // Safe budget: 90% of max input
    private static final int SAFE_TOKENS = (int) (MAX_INPUT_TOKENS * 0.9); // 28800

    // Budget allocation
    private static final int NEWS_BUDGET = (int) (SAFE_TOKENS * 0.40);   // 11520
    private static final int POST_BUDGET = (int) (SAFE_TOKENS * 0.50);   // 14400
    private static final int PREV_BUDGET = (int) (SAFE_TOKENS * 0.10);   // 2880

    // Limits
    private static final int MAX_POSTS = 200;
    private static final int MAX_NEWS = 200;
    private static final int TOP_COMMENTS_PER_POST = 20;
    
    // Time windows for fixed-window fetch
    private static final int POST_DATA_HOURS = 6;
    private static final int NEWS_DATA_HOURS = 6;

    // Predefined event categories (20 categories + 兜底)
    private static final String[] EVENT_CATEGORIES = {
        "军事", "贸易", "外交", "科技", "人权",
        "社会", "经济", "政治", "台海", "港澳",
        "南海", "网络安全", "军售", "制裁", "能源",
        "教育", "环境", "金融", "移民", "其他"
    };

    @Autowired
    private JdbcTemplate jdbc;

    private final ObjectMapper mapper = new ObjectMapper();

    // Shared HttpClient instance (reused across all calls to avoid resource exhaustion)
    private final HttpClient httpClient = HttpClient.newBuilder()
        .connectTimeout(java.time.Duration.ofSeconds(10))
        .build();

    // ==================== Main Entry ====================

    public boolean generate(int hours) {
        log.info("[AI Summary] === Starting report generation (V5 JSON) ===");

        if (!checkOllama()) {
            log.warn("[AI Summary] Ollama unavailable, skipping");
            return false;
        }

        LocalDateTime now = LocalDateTime.now();
        try {
            // ---- Fixed-window data fetch ----
            List<Map<String, Object>> posts = fetchPosts(now);
            List<Map<String, Object>> news = fetchNews(now);
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
                log.info("[AI Summary] No fresh data, skipping generation");
                saveSkippedSummary(now, posts.size(), news.size());
                return true;
            }

            // ---- Build commentsByPost for pre-summarize and buildDataSection ----
            Map<String, List<Map<String, Object>>> commentsByPost = new LinkedHashMap<>();
            for (Map<String, Object> c : comments) {
                String pid = str(c.get("post_id"));
                commentsByPost.computeIfAbsent(pid, k -> new ArrayList<>()).add(c);
            }

            // ---- Pre-summarize ALL content via Ollama (qwen3.5 4b, fast) ----
            preSummarizeAllContent(posts, "post", commentsByPost);
            preSummarizeAllContent(news, "news", commentsByPost);

            // ---- Build prompt (now only asks for summary + risk) ----
            String dataSection = buildDataSection(posts, comments, news);

            // ---- Save pre-summaries to DB for reuse ----
            savePreSummaries(posts, news);

            int newsCount = news.size();
            int postCount = posts.size();
            int commentCount = comments.size();

            // ---- Extract structured stats from raw data (Java code, no LLM) ----
            Map<String, Object> categories = extractCategoryStats(posts, news);
            String categoryCounts = buildCategoryCountsJson(categories);
            Map<String, Object> stats = extractKeywordStats(posts, news);
            Map<String, Object> trends = extractTrends(posts, news, now);
            String riskFromTrend = assessRiskFromTrends(trends);

            // ---- Build short prompt for 27B model (summary only) ----
            String prompt = buildSummaryPrompt(newsCount, postCount, commentCount, prevSummary, dataSection);
            int promptTokens = estimateTokens(prompt);
            log.info("[AI Summary] Summary prompt estimated tokens: {} / {} (safe budget)", promptTokens, SAFE_TOKENS);

            // ---- Call Ollama: 27B generates ONLY short summary + title ----
            long startMs = System.currentTimeMillis();
            String llmOutput = callOllama(prompt, GENERATION_MODEL, 300);
            if (llmOutput == null) {
                log.warn("[AI Summary] Ollama call failed");
                return false;
            }
            int genSeconds = (int) ((System.currentTimeMillis() - startMs) / 1000);
            log.info("[AI Summary] Summary generation complete ({}s, {} chars)", genSeconds, llmOutput.length());

            // ---- Parse LLM output: extract title + summary text ----
            String title = extractTitle(llmOutput);
            String summaryText = extractSummaryText(llmOutput);
            String riskLevel = riskFromTrend != null ? riskFromTrend : extractRisk(llmOutput);
            String riskReason = extractRiskReason(llmOutput);
            List<String> suggestions = extractSuggestions(llmOutput);
            String change = extractChange(llmOutput);

            log.info("[AI Summary] Title: {}", title);
            log.info("[AI Summary] Risk: {} - {}", riskLevel, riskReason);

            // ---- Build structured JSON output ----
            String jsonContent = buildStructuredJson(title, summaryText, riskLevel, riskReason, change, categories, stats, trends, suggestions);

            saveSummary(title, jsonContent, riskLevel, now, newsCount, postCount, genSeconds, categoryCounts);
            log.info("[AI Summary] === V5 Done (structured JSON) ===");
            return true;
        } catch (Exception e) {
            log.error("[AI Summary] Generation failed with exception: {}", e.getMessage());
            try {
                saveSkippedSummary(now, 0, 0);
            } catch (Exception ignored) {}
            return false;
        }
    }

    // ==================== Fixed-Window Data Fetching ====================

    /**
     * Fetch posts within the fixed POST_DATA_HOURS window (6h).
     */
    private List<Map<String, Object>> fetchPosts(LocalDateTime now) {
        String sql = "SELECT post_id, title, author, site_name, like_count, comment_count, "
            + "content, pre_summary, trigger_keyword, crawl_time, category, heat FROM social_post "
            + "WHERE crawl_time > ? ORDER BY like_count DESC, crawl_time DESC LIMIT ?";

        LocalDateTime cutoff = now.minusHours(POST_DATA_HOURS);
        List<Map<String, Object>> all = jdbc.queryForList(sql,
            java.sql.Timestamp.valueOf(cutoff), MAX_POSTS);
        log.info("[AI Summary] Posts ({}h): {}", POST_DATA_HOURS, all.size());
        return all;
    }

    /**
     * Fetch news within the fixed NEWS_DATA_HOURS window (24h).
     */
    private List<Map<String, Object>> fetchNews(LocalDateTime now) {
        String sql = "SELECT title, source, keywords, content, publish_date, crawl_time, category "
            + "FROM news_article WHERE crawl_time > ? ORDER BY crawl_time DESC LIMIT ?";

        LocalDateTime cutoff = now.minusHours(NEWS_DATA_HOURS);
        List<Map<String, Object>> all = jdbc.queryForList(sql,
            java.sql.Timestamp.valueOf(cutoff), MAX_NEWS);
        log.info("[AI Summary] News ({}h): {}", NEWS_DATA_HOURS, all.size());
        return all;
    }

    // ==================== Comments & Previous ====================

    private List<Map<String, Object>> fetchPostComments(List<String> postIds) {
        if (postIds.isEmpty()) return Collections.emptyList();

        String placeholders = String.join(",", Collections.nCopies(postIds.size(), "?"));
        String sql = "SELECT sc.post_id, sc.commenter, sc.comment_content, "
            + "sc.like_count AS comment_likes, "
            + "sp.title AS post_title, sp.author AS post_author, sp.site_name "
            + "FROM social_comment sc JOIN social_post sp ON sc.post_id = sp.post_id "
            + "WHERE sc.post_id IN (" + placeholders + ") "
            + "ORDER BY sc.like_count DESC";

        List<Map<String, Object>> allComments = jdbc.queryForList(sql, postIds.toArray());

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

    private Map<String, Object> fetchPreviousSummary() {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT title, risk_level, content, news_count, social_count, "
            + "DATE_FORMAT(create_time, '%m-%d %H:%i') AS create_time "
            + "FROM ai_summary WHERE summary_type != 'skipped' "
            + "ORDER BY id DESC LIMIT 1"
        );
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ==================== Freshness Check ====================

    private boolean hasFreshPosts(List<Map<String, Object>> posts) {
        if (posts.isEmpty()) return false;
        LocalDateTime cutoff = LocalDateTime.now().minusHours(POST_DATA_HOURS);
        for (Map<String, Object> p : posts) {
            Object ct = p.get("crawl_time");
            if (ct != null) {
                LocalDateTime crawlTime = parseDateTime(ct);
                if (crawlTime != null && crawlTime.isAfter(cutoff)) return true;
            }
        }
        return false;
    }

    private boolean hasFreshNews(List<Map<String, Object>> news) {
        if (news.isEmpty()) return false;
        LocalDateTime cutoff = LocalDateTime.now().minusHours(NEWS_DATA_HOURS);
        for (Map<String, Object> n : news) {
            Object ct = n.get("crawl_time");
            if (ct != null) {
                LocalDateTime crawlTime = parseDateTime(ct);
                if (crawlTime != null && crawlTime.isAfter(cutoff)) return true;
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
            newsCount, postCount, GENERATION_MODEL, 0
        );
    }

    // ==================== Pre-summarization (qwen3.5 4b) ====================

    /**
     * ALL items get LLM pre-summary regardless of content length.
     * Uses qwen3.5 4b (fully in VRAM, ~2s per call) for speed.
     */
    private void preSummarizeAllContent(List<Map<String, Object>> items, String type, Map<String, List<Map<String, Object>>> commentsByPost) {
        int processed = 0;
        int total = items.size();
        // Build commentsByPost from parameter if not provided
        if (commentsByPost == null) {
            commentsByPost = new LinkedHashMap<>();
        }
        for (Map<String, Object> item : items) {
            String title = str(item.get("title"));
            String content = str(item.get("content"));
            if (content.isEmpty() && title.isEmpty()) continue;
            // Check if pre_summary already cached in DB
            String cachedSummary = str(item.get("pre_summary"));
            if (!cachedSummary.isEmpty()) {
                item.put("_pre_summary", cachedSummary);
                continue; // Skip LLM call, use cached
            }

                processed++;
                log.info("[AI Summary] Pre-summarizing {}/{} {} ({} tokens): {}",
                    processed, total, type, estimateTokens(content), truncate(title, 60));
                // Build full context for pre-summary (includes engagement + comments)
            String fullContext;
            if ("post".equals(type)) {
                StringBuilder ctx = new StringBuilder();
                ctx.append("标题: ").append(title);
                int likes = intVal(item.get("like_count"));
                int commentCount = intVal(item.get("comment_count"));
                String keyword = str(item.get("trigger_keyword"));
                ctx.append("\n互动: ").append(likes).append("赞 ").append(commentCount).append("评 | 关键词: ").append(keyword);
                ctx.append("\n正文: ").append(content);
                String postPid = str(item.get("post_id"));
                List<Map<String, Object>> postComments = commentsByPost.getOrDefault(postPid, Collections.emptyList());
                if (!postComments.isEmpty()) {
                    ctx.append("\n热门评论:");
                    int idx = 1;
                    for (Map<String, Object> c : postComments) {
                        ctx.append("\n  ").append(idx++).append(". ").append(str(c.get("commenter")))
                          .append(" (").append(intVal(c.get("comment_likes"))).append("赞): ")
                          .append(str(c.get("comment_content")));
                        if (idx > 10) break;
                    }
                }
                fullContext = ctx.toString();
            } else {
                fullContext = "标题: " + title + "\n正文: " + content;
            }
            String rawResult = callPreSummarize(fullContext, type);
            if (rawResult != null && !rawResult.isEmpty()) {
                Map<String, String> parsed = parsePreSummaryResult(rawResult);
                item.put("_pre_summary", parsed.get("summary"));
                item.put("_category", parsed.get("category"));
            }
            // 计算热度 = 点赞 + 评论数*2 + 所有评论的点赞之和
            int likes = intVal(item.get("like_count"));
            int commentCount = intVal(item.get("comment_count"));
            int commentLikes = 0;
            String postPid = str(item.get("post_id"));
            List<Map<String, Object>> postComments = commentsByPost.getOrDefault(postPid, Collections.emptyList());
            for (Map<String, Object> c : postComments) {
                commentLikes += intVal(c.get("comment_likes"));
            }
            int heat = likes + commentCount * 2 + commentLikes;
            item.put("heat", heat);
        }
        if (processed > 0) {
            log.info("[AI Summary] Pre-summarized {} {} items with {}", processed, type, PRE_SUMMARY_MODEL);
        }
    }

    private String callPreSummarize(String fullContext, String type) {
        String catList = String.join("、", EVENT_CATEGORIES);
        String prompt;
        if ("news".equals(type)) {
            prompt = "请为以下新闻生成150字以内的核心摘要，并给出分类。\n"
                + "分类必须是以下之一：" + catList + "\n"
                + "请按以下格式输出（两行）：\n"
                + "摘要文本\n"
                + "分类: xxx\n\n"
                + fullContext;
        } else {
            prompt = "请为以下帖子生成100字以内的核心摘要，并给出分类。\n"
                + "分类必须是以下之一：" + catList + "\n"
                + "请按以下格式输出（两行）：\n"
                + "摘要文本\n"
                + "分类: xxx\n\n"
                + fullContext;
        }

        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("model", PRE_SUMMARY_MODEL);
            body.put("keep_alive", -1);
            body.put("think", false);
            body.put("messages", List.of(
                Map.of("role", "system", "content",
                    "你是一个文本摘要和分类助手。请用简洁的中文概括以下内容的核心要点，并给出正确的分类。直接输出摘要和分类，不要思考过程。"),
                Map.of("role", "user", "content", prompt)
            ));
            body.put("stream", false);
            String json = mapper.writeValueAsString(body);

            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/chat"))
                .timeout(java.time.Duration.ofSeconds(120))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();

            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
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

    private String buildDataSection(List<Map<String, Object>> posts,
                                     List<Map<String, Object>> comments,
                                     List<Map<String, Object>> news) {
        StringBuilder sb = new StringBuilder();
        int totalBudget = SAFE_TOKENS; // 28800

        // 1. 前次摘要（优先保留）
        Map<String, List<Map<String, Object>>> commentsByPost = new LinkedHashMap<>();
        for (Map<String, Object> c : comments) {
            String pid = str(c.get("post_id"));
            commentsByPost.computeIfAbsent(pid, k -> new ArrayList<>()).add(c);
        }

        // 2. 新闻摘要（优先级第二）
        int newsTokens = 0;
        if (!news.isEmpty()) {
            sb.append("=== 新闻资讯 ===\n");
            for (Map<String, Object> n : news) {
                String item = formatNews(n);
                int t = estimateTokens(item);
                if (newsTokens + t > totalBudget) break;
                sb.append(item).append("\n");
                newsTokens += t;
            }
        }
        totalBudget -= newsTokens;

        // 3. 社交媒体摘要（优先级第三，按热度降序）
        if (!posts.isEmpty()) {
            // 按热度降序排列
            posts.sort((a, b) -> intVal(b.get("heat")) - intVal(a.get("heat")));

            sb.append("\n=== 社交帖子（按热度排序） ===\n");
            for (Map<String, Object> p : posts) {
                String pid = str(p.get("post_id"));
                List<Map<String, Object>> postComments = commentsByPost.getOrDefault(pid, Collections.emptyList());
                String block = buildPostBlock(p, postComments);
                int blockTokens = estimateTokens(block);
                if (blockTokens > totalBudget) break;
                sb.append(block).append("\n");
                totalBudget -= blockTokens;
            }
        }
        return sb.toString();
    }

    private String buildPostBlock(Map<String, Object> post, List<Map<String, Object>> comments) {
        String site = str(post.get("site_name"));
        String author = str(post.get("author"));
        String title = str(post.get("title"));
        int heat = intVal(post.get("heat"));
        String keyword = str(post.get("trigger_keyword"));
        String cat = str(post.get("category"));

        String content;
        Object preSummary = post.get("_pre_summary");
        content = preSummary != null ? str(preSummary) : str(post.get("content"));

        StringBuilder sb = new StringBuilder();
        sb.append("[").append(site).append("] @").append(author).append(": ").append(title).append("\n");
        sb.append("热度: ").append(heat).append(" | 分类: ").append(cat).append(" | 关键词: ").append(keyword).append("\n");
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("内容: ").append(content).append("\n");
        }
        // 评论部分保持不变
        if (!comments.isEmpty()) {
            sb.append("  热门评论:\n");
            int idx = 1;
            for (Map<String, Object> c : comments) {
                String commenter = str(c.get("commenter"));
                String commentContent = str(c.get("comment_content"));
                int cLikes = intVal(c.get("comment_likes"));
                sb.append("  ").append(idx++).append(". ").append(commenter)
                  .append(" (👍").append(cLikes).append("): ").append(commentContent).append("\n");
            }
        }

        return sb.toString();
    }

    private String formatNews(Map<String, Object> n) {
        String title = str(n.get("title"));
        String source = str(n.get("source"));
        String keywords = str(n.get("keywords"));

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
            sb.append("关键词: ").append(keywords).append("\n");
        }
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("正文: ").append(content).append("\n");
        }
        return sb.toString();
    }

    // ==================== Prompt Construction ====================

    /**
     /**
      * V5: Short prompt - only asks 27B to generate a brief summary (3-5 sentences).
      * Structured data (categories, stats, trends) are extracted by Java code.
      */
    private String buildSummaryPrompt(int newsCount, int postCount, int commentCount,
                               Map<String, Object> prevSummary, String dataSection) {
        StringBuilder sb = new StringBuilder();
        String prevSection = buildPrevSection(prevSummary);

        sb.append("请根据以下舆情数据，生成一份简短的舆情分析报告。\n\n");
        sb.append("要求：\n");
        sb.append("1. 用3-5句话写核心摘要，概括最重要的事件、趋势和风险\n");
        sb.append("2. 给出简短标题（一句话）\n");
        sb.append("3. 给出风险评级（低/中/高）及具体原因\n");
        if (!prevSection.isEmpty()) {
            sb.append("4. 与上次简报对比，说明本次的主要变化（新增了什么重要议题、风险趋势如何变化）\n\n");
            sb.append(prevSection).append("\n\n");
        } else {
            sb.append("\n");
        }
        sb.append("数据概览：").append(newsCount).append("条新闻，").append(postCount).append("条社交帖子。\n\n");
        sb.append("数据内容：\n");
        sb.append(dataSection).append("\n\n");
        sb.append("请按以下格式输出（每项一行）：\n");
        sb.append("TITLE: 标题\n");
        sb.append("RISK: 高/中/低\n");
        sb.append("REASON: 风险评级的具体原因（一句话）\n");
        sb.append("SUMMARY: 3-5句核心摘要\n");
        sb.append("CHANGE: 与上次简报的主要变化（1-2句话）\n");
        sb.append("SUGGESTION1: 建议1\n");
        sb.append("SUGGESTION2: 建议2\n");
        sb.append("SUGGESTION3: 建议3\n");
        sb.append("\n请用中文输出，严格按上述格式。\n");
        sb.append("分类只能是以下之一，不能使用其他分类：").append(String.join("、", EVENT_CATEGORIES)).append("\n");
        return sb.toString();
    }

    private String buildPrevSection(Map<String, Object> prev) {
        if (prev == null) return "";
        String title = str(prev.get("title"));
        String risk = str(prev.get("risk_level"));
        String time = str(prev.get("create_time"));
        String content = str(prev.get("content"));

        String summaryText = extractCoreSummary(content);

        return "\n=== 上次简报 (时间: " + time + ") ===\n"
            + "标题: " + title + "\n"
            + "风险等级: " + risk + "\n"
            + "核心摘要: " + truncate(summaryText, 500);
    }

    private String extractCoreSummary(String content) {
        if (content == null || content.isEmpty()) return "";
        String[] lines = content.split("\\n");
        boolean inSummary = false;
        StringBuilder sb = new StringBuilder();
        for (String line : lines) {
            if (line.contains("## 2") || line.contains("核心摘要") || line.contains("Core Summary")) {
                inSummary = true;
                continue;
            }
            if (inSummary && (line.startsWith("## 3") || line.startsWith("## "))) break;
            if (inSummary) sb.append(line).append("\n");
        }
        String result = sb.toString().trim();
        return result.isEmpty() ? truncate(content, 500) : result;
    }

    // ==================== Ollama Calls ====================

    private boolean checkOllama() {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/tags"))
                .timeout(java.time.Duration.ofSeconds(10))
                .GET().build();
            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
            return resp.statusCode() == 200;
        } catch (Exception e) {
            log.warn("[AI Summary] Ollama connection failed: {}", e.getMessage());
            return false;
        }
    }

    /**
     * Generic Ollama chat call. Supports different models.
     */
    private String callOllama(String prompt, String model, int timeoutSeconds) {
        try {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("model", model);
            body.put("keep_alive", -1);
            body.put("think", false);
            body.put("messages", List.of(
                Map.of("role", "system", "content",
                    "你是一个专业的舆情分析师，服务于一个舆情监测平台。请进行深度分析，直接输出简报内容。"),
                Map.of("role", "user", "content", prompt)
            ));
            body.put("stream", false);
            String json = mapper.writeValueAsString(body);

            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/chat"))
                .timeout(java.time.Duration.ofSeconds(timeoutSeconds))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();

            HttpResponse<String> resp = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
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
        // V5: Try structured format first
        String[] lines = content.split("\\n");
        for (String line : lines) {
            if (line.trim().toUpperCase().startsWith("TITLE:")) {
                String title = line.substring(line.indexOf(':') + 1).trim();
                if (!title.isEmpty()) return title;
            }
        }
        // Fallback: old markdown format
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].contains("## 1") || lines[i].contains("Title") || lines[i].contains("标题")) {
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
        // V5: Try structured format first
        String[] lines = content.split("\\n");
        for (String line : lines) {
            if (line.trim().toUpperCase().startsWith("RISK:")) {
                String risk = line.substring(line.indexOf(':') + 1).trim();
                if (risk.contains("高") || risk.toUpperCase().contains("HIGH")) return "高";
                if (risk.contains("低") || risk.toUpperCase().contains("LOW")) return "低";
                return "中";
            }
        }
        // Fallback: old markdown format
        for (String line : lines) {
            if ((line.contains("评级") || line.contains("风险")) && line.startsWith("#")) {
                String upper = line.toUpperCase();
                if (upper.contains("高") || upper.contains("HIGH")) return "高";
                if (upper.contains("低") || upper.contains("LOW")) return "低";
                return "中";
            }
            if (line.contains("评级") || line.contains("风险")) {
                String upper = line.toUpperCase();
                if (upper.contains("高") || upper.contains("HIGH")) return "高";
                if (upper.contains("低") || upper.contains("LOW")) return "低";
                if (upper.contains("中") || upper.contains("MEDIUM")) return "中";
            }
        }
        return "中";
    }

    /**
     * Extract risk reason from LLM output (REASON: line).
     */
    private String extractRiskReason(String content) {
        String[] lines = content.split("\n");
        for (String line : lines) {
            if (line.trim().toUpperCase().startsWith("REASON:")) {
                String reason = line.substring(line.indexOf(':') + 1).trim();
                if (!reason.isEmpty()) return reason;
            }
        }
        return "";
    }

    private void saveSummary(String title, String content, String riskLevel,
                              LocalDateTime dataEnd, int newsCount, int socialCount, int genSeconds,
                              String categoryCounts) {
        jdbc.update(
            "INSERT INTO ai_summary (summary_type, title, content, risk_level, "
            + "data_start, data_end, news_count, social_count, model_name, generate_time, category_counts) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "hourly", title, content, riskLevel,
            java.sql.Timestamp.valueOf(dataEnd), java.sql.Timestamp.valueOf(dataEnd),
            newsCount, socialCount, GENERATION_MODEL, genSeconds, categoryCounts
        );
    }


    /**
     * Save generated pre-summaries back to the database so they can be reused
     * on the next generation run without calling Ollama again.
     */
    private void savePreSummaries(List<Map<String, Object>> posts, List<Map<String, Object>> news) {
        int saved = 0;
        for (Map<String, Object> p : posts) {
            Object pre = p.get("_pre_summary");
            if (pre != null && !str(pre).isEmpty()) {
                try {
                    String category = str(p.get("_category"));
                    if (category.isEmpty()) category = "其他";
                    jdbc.update(
                        "UPDATE social_post SET pre_summary = ?, category = ?, heat = ? WHERE post_id = ? AND (pre_summary IS NULL OR pre_summary = '')",
                        str(pre), category, intVal(p.get("heat")), str(p.get("post_id"))
                    );
                    saved++;
                } catch (Exception e) {
                    log.warn("[AI Summary] Failed to save pre_summary for post: {}", e.getMessage());
                }
            }
        }
        for (Map<String, Object> n : news) {
            Object pre = n.get("_pre_summary");
            if (pre != null && !str(pre).isEmpty()) {
                try {
                    String category = str(n.get("_category"));
                    if (category.isEmpty()) category = "其他";
                    jdbc.update(
                        "UPDATE news_article SET pre_summary = ?, category = ? WHERE title = ? AND (pre_summary IS NULL OR pre_summary = '')",
                        str(pre), category, str(n.get("title"))
                    );
                    saved++;
                } catch (Exception e) {
                    log.warn("[AI Summary] Failed to save pre_summary for news: {}", e.getMessage());
                }
            }
        }
        if (saved > 0) {
            log.info("[AI Summary] Saved {} pre-summaries to database", saved);
        }
    }

    // ==================== V5 Structured JSON Extraction ====================

    /**
     * Extract summary text from LLM output (V5 format: SUMMARY: xxx)
     */
    private String extractSummaryText(String content) {
        if (content == null) return "";
        String[] lines = content.split("\\n");
        for (String line : lines) {
            if (line.trim().toUpperCase().startsWith("SUMMARY:")) {
                return line.substring(line.indexOf(':') + 1).trim();
            }
        }
        // Fallback: return first non-empty paragraph
        for (String line : lines) {
            String trimmed = line.trim();
            if (!trimmed.isEmpty() && !trimmed.startsWith("TITLE:") && !trimmed.startsWith("RISK:")
                && !trimmed.startsWith("SUGGESTION")) {
                return trimmed;
            }
        }
        return truncate(content, 500);
    }

    /**
     * Extract suggestions from LLM output (SUGGESTION1:, SUGGESTION2:, etc.)
     */
    private List<String> extractSuggestions(String content) {
        List<String> suggestions = new ArrayList<>();
        if (content == null) return suggestions;
        String[] lines = content.split("\\n");
        for (String line : lines) {
            String upper = line.trim().toUpperCase();
            if (upper.startsWith("SUGGESTION")) {
                String text = line.substring(line.indexOf(':') + 1).trim();
                if (!text.isEmpty()) suggestions.add(text);
            }
        }
        return suggestions;
    }

    /**
     * Extract change text from LLM output (CHANGE: line).
     */
    private String extractChange(String content) {
        String[] lines = content.split("\n");
        for (String line : lines) {
            if (line.trim().toUpperCase().startsWith("CHANGE:")) {
                String change = line.substring(line.indexOf(':') + 1).trim();
                if (!change.isEmpty()) return change;
            }
        }
        return "";
    }

    /**
     * Extract category statistics from posts and news.
     * Uses DB category field first, falls back to classifyByKeywords.
     * Returns a list of category objects with name, count, trend, and events.
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> extractCategoryStats(List<Map<String, Object>> posts, List<Map<String, Object>> news) {
        Map<String, Integer> categoryCount = new LinkedHashMap<>();
        Map<String, List<String>> categoryEvents = new LinkedHashMap<>();

        // Process posts
        for (Map<String, Object> post : posts) {
            String category = str(post.get("category"));
            if (category.isEmpty()) {
                category = classifyByKeywords(
                    str(post.get("trigger_keyword")) + " " + str(post.get("title"))
                );
            }
            categoryCount.merge(category, 1, Integer::sum);
            categoryEvents.computeIfAbsent(category, k -> new ArrayList<>());
            if (categoryEvents.get(category).size() < 3) {
                categoryEvents.get(category).add(truncate(str(post.get("title")), 80));
            }
        }

        // Process news
        for (Map<String, Object> n : news) {
            String category = str(n.get("category"));
            if (category.isEmpty()) {
                category = classifyByKeywords(
                    str(n.get("keywords")) + " " + str(n.get("title"))
                );
            }
            categoryCount.merge(category, 1, Integer::sum);
            categoryEvents.computeIfAbsent(category, k -> new ArrayList<>());
            if (categoryEvents.get(category).size() < 3) {
                categoryEvents.get(category).add(truncate(str(n.get("title")), 80));
            }
        }

        // Build output
        List<Map<String, Object>> categories = new ArrayList<>();
        for (Map.Entry<String, Integer> entry : categoryCount.entrySet()) {
            Map<String, Object> cat = new LinkedHashMap<>();
            cat.put("name", entry.getKey());
            cat.put("count", entry.getValue());
            cat.put("trend", "持平"); // simplified - could compare with previous
            cat.put("events", categoryEvents.getOrDefault(entry.getKey(), Collections.emptyList()));
            categories.add(cat);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("categories", categories);
        return result;
    }

    /**
     * Extract keyword frequency statistics from posts and news.
     */
    private Map<String, Object> extractKeywordStats(List<Map<String, Object>> posts, List<Map<String, Object>> news) {
        Map<String, Integer> keywordFreq = new LinkedHashMap<>();

        // Extract from posts
        for (Map<String, Object> post : posts) {
            String keyword = str(post.get("trigger_keyword"));
            if (!keyword.isEmpty()) {
                // Split by common delimiters
                for (String kw : keyword.split("[,，、;；\\s]+")) {
                    kw = kw.trim();
                    if (!kw.isEmpty() && kw.length() > 1) {
                        keywordFreq.merge(kw, 1, Integer::sum);
                    }
                }
            }
        }

        // Extract from news keywords
        for (Map<String, Object> n : news) {
            String keywords = str(n.get("keywords"));
            if (!keywords.isEmpty()) {
                for (String kw : keywords.split("[,，、;；\\s]+")) {
                    kw = kw.trim();
                    if (!kw.isEmpty() && kw.length() > 1) {
                        keywordFreq.merge(kw, 1, Integer::sum);
                    }
                }
            }
        }

        // Sort by frequency, take top 20
        List<Map<String, Object>> topKeywords = new ArrayList<>();
        keywordFreq.entrySet().stream()
            .sorted(Map.Entry.<String, Integer>comparingByValue().reversed())
            .limit(20)
            .forEach(e -> {
                Map<String, Object> kw = new LinkedHashMap<>();
                kw.put("word", e.getKey());
                kw.put("count", e.getValue());
                topKeywords.add(kw);
            });

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("total_posts", posts.size());
        result.put("total_news", news.size());
        result.put("keywords", topKeywords);
        return result;
    }

    /**
     * Extract trend data: time-series category counts and risk trend from recent summaries.
     * Time labels are generated in 6-hour intervals for the last 48 hours (8 points).
     * Uses DB category field first, falls back to classifyByKeywords.
     */
    private Map<String, Object> extractTrends(List<Map<String, Object>> posts, List<Map<String, Object>> news, LocalDateTime now) {
        Map<String, Object> trends = new LinkedHashMap<>();

        // Generate time labels (6h intervals for last 48h = 8 points)
        List<String> timeLabels = new ArrayList<>();
        for (int i = 7; i >= 0; i--) {
            LocalDateTime t = now.minusHours(i * 6);
            timeLabels.add(t.format(DateTimeFormatter.ofPattern("MM-dd HH")));
        }
        trends.put("time_labels", timeLabels);

        // Initialize time series for all categories
        Map<String, int[]> categoryTimeSeries = new LinkedHashMap<>();
        for (String cat : EVENT_CATEGORIES) {
            categoryTimeSeries.put(cat, new int[8]);
        }

        // Count posts per time bucket
        for (Map<String, Object> post : posts) {
            LocalDateTime crawlTime = parseDateTime(post.get("crawl_time"));
            if (crawlTime == null) continue;
            int bucket = getBucketIndex(crawlTime, now);
            if (bucket < 0 || bucket >= 8) continue;

            String category = str(post.get("category"));
            if (category.isEmpty()) {
                category = classifyByKeywords(
                    str(post.get("trigger_keyword")) + " " + str(post.get("title"))
                );
            }
            int[] series = categoryTimeSeries.get(category);
            if (series != null) {
                series[bucket]++;
            } else {
                categoryTimeSeries.computeIfAbsent("其他", k -> new int[8])[bucket]++;
            }
        }

        // Count news per time bucket
        for (Map<String, Object> n : news) {
            LocalDateTime crawlTime = parseDateTime(n.get("crawl_time"));
            if (crawlTime == null) continue;
            int bucket = getBucketIndex(crawlTime, now);
            if (bucket < 0 || bucket >= 8) continue;

            String category = str(n.get("category"));
            if (category.isEmpty()) {
                category = classifyByKeywords(
                    str(n.get("keywords")) + " " + str(n.get("title"))
                );
            }
            int[] series = categoryTimeSeries.get(category);
            if (series != null) {
                series[bucket]++;
            } else {
                categoryTimeSeries.computeIfAbsent("其他", k -> new int[8])[bucket]++;
            }
        }

        // Build category_trends
        Map<String, List<Integer>> categoryTrends = new LinkedHashMap<>();
        for (Map.Entry<String, int[]> entry : categoryTimeSeries.entrySet()) {
            int total = 0;
            for (int v : entry.getValue()) total += v;
            if (total > 0) {
                List<Integer> values = new ArrayList<>();
                for (int v : entry.getValue()) {
                    values.add(v);
                }
                categoryTrends.put(entry.getKey(), values);
            }
        }
        trends.put("category_trends", categoryTrends);

        // Fetch recent risk trends from DB (last 8 summaries)
        List<String> riskTrend = fetchRecentRiskTrends(8);
        trends.put("risk_trend", riskTrend);

        return trends;
    }

    /**
     * Get time bucket index: 0=oldest, 7=newest (6h buckets over 48h)
     */
    private int getBucketIndex(LocalDateTime crawlTime, LocalDateTime now) {
        long hoursAgo = java.time.Duration.between(crawlTime, now).toHours();
        if (hoursAgo < 0) hoursAgo = 0;
        int bucket = 7 - (int)(hoursAgo / 6);
        return Math.max(0, Math.min(7, bucket));
    }

    /**
     * Fetch recent risk levels from DB for trend display.
     */
    private List<String> fetchRecentRiskTrends(int count) {
        List<String> risks = new ArrayList<>();
        try {
            List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT risk_level FROM ai_summary WHERE summary_type != 'skipped' "
                + "ORDER BY id DESC LIMIT ?", count
            );
            for (Map<String, Object> row : rows) {
                risks.add(str(row.get("risk_level")));
            }
        } catch (Exception e) {
            log.warn("[AI Summary] Failed to fetch risk trends: {}", e.getMessage());
        }
        Collections.reverse(risks); // chronological order
        return risks;
    }

    /**
     * Assess risk level based on trend data (volume + risk history).
     */
    private String assessRiskFromTrends(Map<String, Object> trends) {
        @SuppressWarnings("unchecked")
        List<String> riskTrend = (List<String>) trends.get("risk_trend");
        if (riskTrend == null || riskTrend.isEmpty()) return null;

        // Count risk levels in recent history
        int highCount = 0, midCount = 0;
        for (String r : riskTrend) {
            if ("高".equals(r)) highCount++;
            else if ("中".equals(r)) midCount++;
        }
        if (highCount >= 2) return "高";
        if (midCount >= 2 || highCount >= 1) return "中";
        return "低";
    }

    /**
     * Build the final structured JSON content string.
     */
    private String buildStructuredJson(String title, String summaryText, String riskLevel, String riskReason,
                                    String change, Map<String, Object> categories, Map<String, Object> stats,
                                    Map<String, Object> trends, List<String> suggestions) {
        try {
            Map<String, Object> json = new LinkedHashMap<>();
            json.put("title", title);
            json.put("risk_level", riskLevel);
            json.put("risk_reason", riskReason);
            json.put("summary", summaryText);
            if (change != null && !change.isEmpty()) {
                json.put("change", change);
            }
            json.put("categories", categories.get("categories"));
            // Build category_counts as a Map (not String) to avoid double-serialization
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> cats = (List<Map<String, Object>>) categories.get("categories");
            Map<String, Integer> categoryCountsMap = new LinkedHashMap<>();
            if (cats != null) {
                for (Map<String, Object> cat : cats) {
                    categoryCountsMap.put(str(cat.get("name")), intVal(cat.get("count")));
                }
            }
            json.put("category_counts", categoryCountsMap);
            json.put("stats", stats);
            json.put("trends", trends);
            json.put("suggestions", suggestions);
            return mapper.writerWithDefaultPrettyPrinter().writeValueAsString(json);
        } catch (Exception e) {
            log.error("[AI Summary] Failed to build JSON: {}", e.getMessage());
            return "{}";
        }
    }

    // ==================== Category Classification ====================

    /**
     * Parse pre-summary result: "摘要文本\n分类: xxx" format.
     * Returns map with keys "summary" and "category".
     */
    private Map<String, String> parsePreSummaryResult(String raw) {
        Map<String, String> result = new LinkedHashMap<>();
        if (raw == null || raw.isEmpty()) {
            result.put("summary", "");
            result.put("category", "其他");
            return result;
        }
        String[] lines = raw.split("\n");
        StringBuilder summaryBuilder = new StringBuilder();
        String category = "其他";
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.startsWith("分类:") || trimmed.startsWith("分类：")) {
                String cat = trimmed.replaceFirst("^分类[:：]\\s*", "").trim();
                if (isValidCategory(cat)) {
                    category = cat;
                }
            } else if (!trimmed.isEmpty()) {
                if (summaryBuilder.length() > 0) summaryBuilder.append("\n");
                summaryBuilder.append(trimmed);
            }
        }
        result.put("summary", summaryBuilder.toString().trim());
        result.put("category", category);
        return result;
    }

    /**
     * Check if category is a valid predefined category.
     */
    private boolean isValidCategory(String category) {
        if (category == null || category.isEmpty()) return false;
        for (String c : EVENT_CATEGORIES) {
            if (c.equals(category)) return true;
        }
        return false;
    }

    /**
     * Fallback keyword-based classification when DB category is empty.
     */
    private String classifyByKeywords(String text) {
        if (text == null || text.isEmpty()) return "其他";
        String[][] categoryKeywords = {
            {"军事", "军演", "导弹", "军舰", "战机", "武器", "国防", "军队", "航母", "海军", "空军", "陆军"},
            {"贸易", "关税", "进出口", "制裁", "出口", "进口", "贸易战", "贸易壁垒"},
            {"外交", "大使", "会晤", "峰会", "协议", "条约", "联合国", "访问", "会谈"},
            {"科技", "芯片", "半导体", "AI", "人工智能", "5G", "量子", "技术", "研发", "专利"},
            {"人权", "自由", "民主", "维权", "抗议", "言论", "新闻自由"},
            {"社会", "民生", "教育", "医疗", "就业", "房价", "疫情", "灾害"},
            {"经济", "GDP", "通胀", "利率", "股市", "汇率", "增长", "衰退"},
            {"政治", "选举", "政党", "议会", "政府", "政策", "改革", "执政"},
            {"台海", "台湾", "台海", "两岸", "统一", "台独"},
            {"港澳", "香港", "澳门", "港独", "一国两制"},
            {"南海", "南海", "南沙", "岛礁", "航行自由"},
            {"网络安全", "黑客", "网络攻击", "数据泄露", "网络安全"},
            {"军售", "军售", "武器出口", "军火"},
            {"制裁", "制裁", "禁令", "黑名单"},
            {"能源", "石油", "天然气", "能源", "新能源", "碳排放"},
            {"环境", "环境", "气候", "碳中和", "污染"},
            {"金融", "金融", "银行", "投资", "资本"},
            {"移民", "移民", "难民", "签证", "边境"},
            {"教育", "教育", "高校", "科研", "学术"}
        };
        for (String[] ck : categoryKeywords) {
            for (int i = 0; i < ck.length; i++) {
                if (text.contains(ck[i])) {
                    return ck[0];
                }
            }
        }
        return "其他";
    }

    /**
     * Build categoryCounts JSON string from category stats.
     */
    private String buildCategoryCountsJson(Map<String, Object> categories) {
        try {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> cats = (List<Map<String, Object>>) categories.get("categories");
            Map<String, Integer> counts = new LinkedHashMap<>();
            if (cats != null) {
                for (Map<String, Object> cat : cats) {
                    counts.put(str(cat.get("name")), intVal(cat.get("count")));
                }
            }
            return counts.toString();
        } catch (Exception e) {
            return "{}";
        }
    }

    // ==================== Utility Methods ====================

    private List<String> extractPostIds(List<Map<String, Object>> posts) {
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> p : posts) {
            Object pid = p.get("post_id");
            if (pid != null) ids.add(pid.toString());
        }
        return ids;
    }

    /**
     * Estimate total tokens for a list of items WITHOUT comments (not yet fetched).
     * Includes: [site] @author: title \n 互动: X赞 Y评 | 关键词: Z \n 内容: content
     * Used only for deciding whether to fetch more data layers (conservative lower bound).
     * Actual token usage will be higher once comments are attached in buildDataSection.
     */
    private int estimateListTokens(List<Map<String, Object>> items) {
        int total = 0;
        for (Map<String, Object> item : items) {
            // Match buildPostBlock format: [site] @author: title\n互动: X赞 Y评 | 关键词: Z\n内容: content
            String site = str(item.get("site_name"));
            String author = str(item.get("author"));
            String title = str(item.get("title"));
            String content = str(item.get("content"));
            String keyword = str(item.get("trigger_keyword"));
            int likes = intVal(item.get("like_count"));
            int comments = intVal(item.get("comment_count"));
            String block = "[" + site + "] @" + author + ": " + title + "\n"
                + "互动: " + likes + "赞 " + comments + "评 | 关键词: " + keyword + "\n"
                + "内容: " + content;
            total += estimateTokens(block);
        }
        return total;
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
