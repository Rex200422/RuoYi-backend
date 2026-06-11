package com.ruoyi.web.controller.sentiment;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/system/sentiment/crawlLog")
public class CrawlLogController extends BaseController {
    private static final Logger log = LoggerFactory.getLogger(CrawlLogController.class);

    @Autowired private ICrawlLogService svc;

    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list")
    public TableDataInfo list(CrawlLog q) {
        startPage();
        return getDataTable(svc.selectList(q));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable Long id) {
        return success(svc.selectById(id));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids) {
        return toAjax(svc.deleteByIds(ids));
    }

    /**
     * Get crawl summary statistics: total crawls, success count, failure count.
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/stats")
    public AjaxResult stats() {
        Map<String, Long> stats = new HashMap<>();
        stats.put("totalCrawls", svc.selectTotalCount());
        stats.put("successCount", svc.selectSuccessCount());
        stats.put("failedCount", svc.selectFailedCount());
        return success(stats);
    }

    /**
     * 下载爬取任务日志文件。
     * 日志路径格式：crawlers/logs/{siteName}_{logId}.log
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/log/{id}")
    public void downloadLog(@PathVariable Long id, jakarta.servlet.http.HttpServletResponse response) {
        CrawlLog crawlLog = svc.selectById(id);
        if (crawlLog == null) {
            response.setStatus(404);
            return;
        }

        String logFilePath = "/root/workspace/RuoYi-backend/crawlers/logs/"
            + crawlLog.getSiteName() + "_" + id + ".log";

        java.io.File file = new java.io.File(logFilePath);
        if (!file.exists()) {
            response.setStatus(404);
            try {
                response.getWriter().write("日志文件不存在: " + logFilePath);
            } catch (Exception ignored) {}
            return;
        }

        try {
            response.setContentType("text/plain; charset=utf-8");
            response.setHeader("Content-Disposition", "attachment; filename=\""
                + crawlLog.getSiteName() + "_" + id + ".log\"");

            try (java.io.InputStream is = new java.io.FileInputStream(file);
                 java.io.OutputStream os = response.getOutputStream()) {
                byte[] buffer = new byte[4096];
                int bytesRead;
                while ((bytesRead = is.read(buffer)) != -1) {
                    os.write(buffer, 0, bytesRead);
                }
                os.flush();
            }
        } catch (Exception e) {
            log.error("Failed to download log file for id={}: {}", id, e.getMessage());
        }
    }

    /**
     * 预览爬取任务日志文件（返回文本内容）。
     * 支持 tail 参数控制返回行数。
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/log/{id}/preview")
    public AjaxResult previewLog(@PathVariable Long id, @RequestParam(defaultValue = "200") int lines) {
        java.io.File logFile = findLogFile(id);
        if (logFile == null || !logFile.exists()) {
            return AjaxResult.error("日志文件不存在");
        }
        try {
            java.util.List<String> logLines = new java.util.ArrayList<>();
            try (java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.FileReader(logFile))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    logLines.add(line);
                }
            }
            // 返回最后 N 行
            int start = Math.max(0, logLines.size() - lines);
            java.util.List<String> tail = logLines.subList(start, logLines.size());
            Map<String, Object> result = new HashMap<>();
            result.put("content", String.join("\n", tail));
            result.put("totalLines", logLines.size());
            result.put("returnLines", tail.size());
            result.put("fileName", logFile.getName());
            result.put("fileSize", logFile.length());
            return success(result);
        } catch (Exception e) {
            return AjaxResult.error("读取日志失败: " + e.getMessage());
        }
    }

    /**
     * 根据 logId 查找对应的日志文件
     */
    private java.io.File findLogFile(Long logId) {
        String logDir = "/root/workspace/RuoYi-backend/crawlers/logs";
        java.io.File dir = new java.io.File(logDir);
        if (!dir.exists()) return null;

        String[] files = dir.list();
        if (files == null) return null;

        String suffix = "_" + logId + ".log";
        for (String file : files) {
            if (file.endsWith(suffix)) {
                return new java.io.File(dir, file);
            }
        }
        return null;
    }
}
