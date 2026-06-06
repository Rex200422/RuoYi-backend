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
 * AI Summary Generator - V3
 * Layered data fetch (1h->6h->24h for posts, 12h->24h for news)
 * + dual-model pre-summarization (qwen3.5 4b) + generation (qwen3.6 36B)
 * + Thinking Preservation via previous summary in context.
 */
@Service
public class AiSummaryGenerator {
    private static final Logger log = LoggerFactory.getLogger(AiSummaryGenerator.class);

    private static final String OLLAMA_BASE = "http://200m.frpee.com:18138";
    private static final String GENERATION_MODEL = "qwen3.6:27b";
    private static final String PRESUMMARY_MODEL = "qwen3.5:4b-q4_K_M";

    // Token estimation: Chinese ~1.5 tokens/char
    private static final double TOKEN_PER_CHAR = 1.5;
    private static final int MAX_INPUT_TOKENS = 32000;

    // Safe budget: 90% of max input
    private static final int SAFE_TOKENS = (int) (MAX_INPUT_TOKENS * 0.9); // 28800

    // Budget allocation
    private static final int NEWS_BUDGET = (int) (SAFE_TOKENS * 0.40);   // 11520
    private static final int POST_BUDGET = (int) (SAFE_TOKENS * 0.50);   // 14400
    private static final int PREV_BUDGET = (int) (SAFE_TOKENS * 0.10);   // 2880

    // Pre-summarization: unified threshold for both news and posts
    private static final int PRE_SUMMARY_THRESHOLD = 800;

    // Limits
    private static final int MAX_POSTS = 200;
    private static final int MAX_NEWS = 200;
    private static final int TOP_COMMENTS_PER_POST = 20;

    // Time windows for layered fetch
    private static final int POST_FRESH_HOURS = 1;
    private static final int POST_FALLBACK_HOURS_1 = 6;
    private static final int POST_FALLBACK_HOURS_2 = 24;
    private static final int NEWS_FRESH_HOURS = 12;
    private static final int NEWS_FALLBACK_HOURS = 24;

    @Autowired
    private JdbcTemplate jdbc;

    private final ObjectMapper mapper = new ObjectMapper();

    // ==================== Main Entry ====================

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

            // ---- Pre-summarize ALL long content via Ollama (qwen3.5 4b, fast) ----
            preSummarizeLongContent(posts, "post", commentsByPost);
            preSummarizeLongContent(news, "news", commentsByPost);

            // ---- Build prompt ----
            String dataSection = buildDataSection(posts, comments, news);
            int newsCount = news.size();
            int postCount = posts.size();
            int commentCount = comments.size();

            String prompt = buildPrompt(newsCount, postCount, commentCount, prevSummary, dataSection);
            int promptTokens = estimateTokens(prompt);
            log.info("[AI Summary] Prompt estimated tokens: {} / {} (safe budget)", promptTokens, SAFE_TOKENS);

            // ---- Call Ollama (generation model: qwen3.6 36B) ----
            long startMs = System.currentTimeMillis();
            String content = callOllama(prompt, GENERATION_MODEL, 300);
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

    // ==================== Layered Data Fetching ====================

    /**
     * Layer 1: posts within 1h. Layer 2: 1-6h. Layer 3: 6-24h.
     * Each layer fills remaining token budget.
     */
    private List<Map<String, Object>> fetchPostsLayered(LocalDateTime now) {
        String sql = "SELECT post_id, title, author, site_name, like_count, comment_count, "
            + "content, pre_summary, trigger_keyword, crawl_time FROM social_post "
            + "WHERE crawl_time > ? ORDER BY like_count DESC, crawl_time DESC LIMIT ?";

        // Layer 1: 1h
        List<Map<String, Object>> all = new ArrayList<>();
        LocalDateTime cutoff1 = now.minusHours(POST_FRESH_HOURS);
        all.addAll(jdbc.queryForList(sql, java.sql.Timestamp.valueOf(cutoff1), MAX_POSTS));
        log.info("[AI Summary] Posts layer1 (1h): {}", all.size());

        // Layer 2: 6h
        if (estimateListTokens(all) < POST_BUDGET) {
            LocalDateTime cutoff2 = now.minusHours(POST_FALLBACK_HOURS_1);
            List<Map<String, Object>> layer2 = jdbc.queryForList(sql,
                java.sql.Timestamp.valueOf(cutoff2), MAX_POSTS);
            // Deduplicate by post_id
            Set<String> seen = new HashSet<>();
            for (Map<String, Object> p : all) seen.add(str(p.get("post_id")));
            for (Map<String, Object> p : layer2) {
                if (!seen.contains(str(p.get("post_id")))) {
                    all.add(p);
                    seen.add(str(p.get("post_id")));
                }
            }
            log.info("[AI Summary] Posts layer2 (6h): +{} = {}", layer2.size() - (layer2.size() - layer2.stream().filter(p -> !seen.contains(str(p.get("post_id")))).count()), all.size());
        }

        // Layer 3: 24h
        if (estimateListTokens(all) < POST_BUDGET) {
            LocalDateTime cutoff3 = now.minusHours(POST_FALLBACK_HOURS_2);
            List<Map<String, Object>> layer3 = jdbc.queryForList(sql,
                java.sql.Timestamp.valueOf(cutoff3), MAX_POSTS);
            Set<String> seen = new HashSet<>();
            for (Map<String, Object> p : all) seen.add(str(p.get("post_id")));
            for (Map<String, Object> p : layer3) {
                if (!seen.contains(str(p.get("post_id")))) {
                    all.add(p);
                    seen.add(str(p.get("post_id")));
                }
            }
            log.info("[AI Summary] Posts layer3 (24h): total={}", all.size());
        }

        return all;
    }

    /**
     * Layer 1: news within 12h. Layer 2: 24h if token budget allows.
     */
    private List<Map<String, Object>> fetchNewsLayered(LocalDateTime now) {
        String sql = "SELECT title, source, keywords, content, publish_date, crawl_time "
            + "FROM news_article WHERE crawl_time > ? ORDER BY crawl_time DESC LIMIT ?";

        // Layer 1: 12h
        List<Map<String, Object>> all = new ArrayList<>();
        LocalDateTime cutoff1 = now.minusHours(NEWS_FRESH_HOURS);
        all.addAll(jdbc.queryForList(sql, java.sql.Timestamp.valueOf(cutoff1), MAX_NEWS));
        log.info("[AI Summary] News layer1 (12h): {}", all.size());

        // Layer 2: 24h
        if (estimateListTokens(all) < NEWS_BUDGET) {
            LocalDateTime cutoff2 = now.minusHours(NEWS_FALLBACK_HOURS);
            List<Map<String, Object>> layer2 = jdbc.queryForList(sql,
                java.sql.Timestamp.valueOf(cutoff2), MAX_NEWS);
            Set<String> seen = new HashSet<>();
            for (Map<String, Object> n : all) seen.add(str(n.get("url")) + str(n.get("title")));
            for (Map<String, Object> n : layer2) {
                String key = str(n.get("url")) + str(n.get("title"));
                if (!seen.contains(key)) {
                    all.add(n);
                    seen.add(key);
                }
            }
            log.info("[AI Summary] News layer2 (24h): total={}", all.size());
        }

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
        LocalDateTime cutoff = LocalDateTime.now().minusHours(POST_FRESH_HOURS);
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
        LocalDateTime cutoff = LocalDateTime.now().minusHours(NEWS_FRESH_HOURS);
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
     * ALL items exceeding PRE_SUMMARY_THRESHOLD tokens get LLM pre-summary.
     * No truncation fallback — every long item goes through the presumer.
     * Uses qwen3.5 4b for speed (~2s per call vs ~30s for 36B).
     */
    private void preSummarizeLongContent(List<Map<String, Object>> items, String type, Map<String, List<Map<String, Object>>> commentsByPost) {
        int processed = 0;
        int total = items.size();
        // Build commentsByPost from parameter if not provided
        if (commentsByPost == null) {
            commentsByPost = new LinkedHashMap<>();
        }
        for (Map<String, Object> item : items) {
            String title = str(item.get("title"));
            String content = str(item.get("content"));
            if (content.isEmpty() || content.equals(title)) continue;

            // Check if pre_summary already cached in DB
            String cachedSummary = str(item.get("pre_summary"));
            if (!cachedSummary.isEmpty()) {
                item.put("_pre_summary", cachedSummary);
                continue; // Skip LLM call, use cached
            }

            // For posts: calculate token estimate including ALL fields (title, likes, comments, keyword, content, top10 comments)
            int tokens;
            if ("post".equals(type)) {
                // Build full post block to get accurate token count
                String pid = str(item.get("post_id"));
                List<Map<String, Object>> postComments = commentsByPost.getOrDefault(pid, Collections.emptyList());
                String fullBlock = buildPostBlock(item, postComments);
                tokens = estimateTokens(fullBlock);
            } else {
                tokens = estimateTokens(content);
            }
            if (tokens > PRE_SUMMARY_THRESHOLD) {
                processed++;
                log.info("[AI Summary] Pre-summarizing {}/{} {} ({} tokens): {}",
                    processed, total, type, tokens, truncate(title, 60));
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
                String summary = callPreSummarize(fullContext, type);
                if (summary != null && !summary.isEmpty()) {
                    item.put("_pre_summary", summary);
                }
                // Also pre-summarize associated comments if any are long
                if ("post".equals(type) && item.containsKey("comment_content")) {
                    String commentContent = str(item.get("comment_content"));
                    if (estimateTokens(commentContent) > PRE_SUMMARY_THRESHOLD) {
                        String cs = callPreSummarize("标题: " + str(item.get("post_title")) + "\n评论内容: " + commentContent, "comment");
                        if (cs != null && !cs.isEmpty()) {
                            item.put("_pre_comment_summary", cs);
                        }
                    }
                }
            }
        }
        if (processed > 0) {
            log.info("[AI Summary] Pre-summarized {} {} items with {}", processed, type, PRESUMMARY_MODEL);
        }
    }

    private String callPreSummarize(String fullContext, String type) {
        String prompt;
        if ("news".equals(type)) {
            prompt = "请为以下新闻生成150字以内的核心摘要，保留关键信息（时间、地点、人物、事件、影响）：\n" + fullContext;
        } else {
            prompt = "请为以下帖子生成100字以内的核心摘要，保留关键信息（话题、作者观点、互动热度、评论焦点、关键细节）：\n" + fullContext;
        }

        try {
            Map<String, Object> body = Map.of(
                "model", PRESUMMARY_MODEL,
                "messages", List.of(
                    Map.of("role", "system", "content",
                        "你是一个文本摘要助手。请用简洁的中文概括以下内容的核心要点。"),
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

    private String buildDataSection(List<Map<String, Object>> posts,
                                     List<Map<String, Object>> comments,
                                     List<Map<String, Object>> news) {
        StringBuilder sb = new StringBuilder();
        int usedTokens = 0;

        Map<String, List<Map<String, Object>>> commentsByPost = new LinkedHashMap<>();
        for (Map<String, Object> c : comments) {
            String pid = str(c.get("post_id"));
            commentsByPost.computeIfAbsent(pid, k -> new ArrayList<>()).add(c);
        }

        // ---- Posts + Comments section ----
        if (!posts.isEmpty()) {
            sb.append("=== 社交帖子与评论 ===\n");
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
            sb.append("\n=== 新闻文章 ===\n");
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

    private String buildPostBlock(Map<String, Object> post, List<Map<String, Object>> comments) {
        String site = str(post.get("site_name"));
        String author = str(post.get("author"));
        String title = str(post.get("title"));
        int likes = intVal(post.get("like_count"));
        int commentCount = intVal(post.get("comment_count"));
        String keyword = str(post.get("trigger_keyword"));

        String content;
        Object preSummary = post.get("_pre_summary");
        if (preSummary != null) {
            content = str(preSummary);
        } else {
            content = str(post.get("content"));
        }

        StringBuilder sb = new StringBuilder();
        sb.append("[").append(site).append("] @").append(author).append(": ").append(title).append("\n");
        sb.append("互动: ").append(likes).append("赞 ").append(commentCount)
          .append("评 | 关键词: ").append(keyword).append("\n");
        if (!content.isEmpty() && !content.equals(title)) {
            sb.append("内容: ").append(content).append("\n");
        }

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
     * Build prompt with previous summary embedded as context.
     * The model sees the previous report as part of the conversation history,
     * enabling Thinking Preservation for trend comparison.
     */
    private String buildPrompt(int newsCount, int postCount, int commentCount,
                               Map<String, Object> prevSummary, String dataSection) {
        StringBuilder sb = new StringBuilder();

        // Previous summary as context (for Thinking Preservation)
        String prevSection = buildPrevSection(prevSummary);

        sb.append("请根据以下舆情数据，生成一份专业的舆情监测简报。\n\n");

        sb.append("数据概览：").append(newsCount).append("条新闻，").append(postCount).append("条社交帖子。\n");

        if (!prevSection.isEmpty()) {
            sb.append(prevSection).append("\n");
        }

        sb.append("\n数据内容：\n");
        sb.append(dataSection).append("\n\n");

        sb.append("请严格按照以下格式输出 Markdown 简报：\n\n");
        sb.append("## 1. 标题\n用一句话概括本时段舆情态势\n\n");
        sb.append("## 2. 核心摘要\n3-5句话总结最重要的事件和趋势\n\n");
        sb.append("## 3. 分类统计\n按主题分类（军事、贸易、人权、外交、科技、社会等），每个分类列出关键事件\n\n");
        sb.append("## 4. 热门互动分析\n分析点赞/评论互动最高的帖子和评论，总结舆论焦点\n\n");
        sb.append("## 5. 舆情变化对比\n与上一次简报对比，分析风险趋势变化（升高/持平/下降），新增的重要议题\n\n");
        sb.append("## 6. 风险评级\n评级：低/中/高\n\n说明理由\n\n");
        sb.append("## 7. 关注建议\n下一步需要重点关注的方向（3-5条）\n\n");
        sb.append("请用中文输出。");

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

    /**
     * Generic Ollama chat call. Supports different models.
     */
    private String callOllama(String prompt, String model, int timeoutSeconds) {
        try {
            Map<String, Object> body = Map.of(
                "model", model,
                "messages", List.of(
                    Map.of("role", "system", "content",
                        "你是一个专业的舆情分析师，服务于一个舆情监测平台。"),
                    Map.of("role", "user", "content", prompt)
                ),
                "stream", false
            );
            String json = mapper.writeValueAsString(body);

            HttpClient client = HttpClient.newHttpClient();
            HttpRequest req = HttpRequest.newBuilder()
                .uri(URI.create(OLLAMA_BASE + "/api/chat"))
                .timeout(java.time.Duration.ofSeconds(timeoutSeconds))
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
        String[] lines = content.split("\\n");
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
        String[] lines = content.split("\\n");
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

    private void saveSummary(String title, String content, String riskLevel,
                              LocalDateTime dataEnd, int newsCount, int socialCount, int genSeconds) {
        jdbc.update(
            "INSERT INTO ai_summary (summary_type, title, content, risk_level, "
            + "data_start, data_end, news_count, social_count, model_name, generate_time) "
            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            "hourly", title, content, riskLevel,
            java.sql.Timestamp.valueOf(dataEnd), java.sql.Timestamp.valueOf(dataEnd),
            newsCount, socialCount, GENERATION_MODEL, genSeconds
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
                    jdbc.update(
                        "UPDATE social_post SET pre_summary = ? WHERE post_id = ? AND (pre_summary IS NULL OR pre_summary = '')",
                        str(pre), str(p.get("post_id"))
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
                    jdbc.update(
                        "UPDATE news_article SET pre_summary = ? WHERE title = ? AND (pre_summary IS NULL OR pre_summary = '')",
                        str(pre), str(n.get("title"))
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

    // ==================== Utility Methods ====================

    private List<String> extractPostIds(List<Map<String, Object>> posts) {
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> p : posts) {
            Object pid = p.get("post_id");
            if (pid != null) ids.add(pid.toString());
        }
        return ids;
    }

    private int estimateListTokens(List<Map<String, Object>> items) {
        int total = 0;
        for (Map<String, Object> item : items) {
            total += estimateTokens(str(item.get("content")));
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
