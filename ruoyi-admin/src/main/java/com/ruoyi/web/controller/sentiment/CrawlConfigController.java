package com.ruoyi.web.controller.sentiment;

import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.CrawlConfig;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.service.sentiment.ICrawlConfigService;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

@RestController
@RequestMapping("/system/sentiment/crawlConfig")
public class CrawlConfigController extends BaseController {
    private static final Logger log = LoggerFactory.getLogger(CrawlConfigController.class);

    @Autowired private ICrawlConfigService crawlConfigService;
    @Autowired private ICrawlLogService crawlLogService;

    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list")
    public TableDataInfo list(CrawlConfig q) {
        startPage();
        return getDataTable(crawlConfigService.selectList(q));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable Long id) {
        return success(crawlConfigService.selectById(id));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:add')")
    @PostMapping
    public AjaxResult add(@RequestBody CrawlConfig config) {
        return toAjax(crawlConfigService.insert(config));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:edit')")
    @PutMapping
    public AjaxResult edit(@RequestBody CrawlConfig config) {
        return toAjax(crawlConfigService.update(config));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids) {
        return toAjax(crawlConfigService.deleteByIds(ids));
    }

    /**
     * Manually trigger a crawl for a specific config.
     * Runs the corresponding Python script as a subprocess asynchronously.
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:edit')")
    @PostMapping("/trigger/{id}")
    public AjaxResult trigger(@PathVariable Long id) {
        CrawlConfig config = crawlConfigService.selectById(id);
        if (config == null) {
            return AjaxResult.error("Crawl config not found");
        }
        if (config.getEnabled() == null || config.getEnabled() != 1) {
            return AjaxResult.error("Crawl config is disabled");
        }

        // Create a crawl_log entry with status=running
        CrawlLog crawlLog = new CrawlLog();
        crawlLog.setSiteName(config.getSiteName());
        crawlLog.setKeyword(config.getKeyword());
        crawlLog.setStatus("running");
        crawlLog.setStartTime(new Date());
        crawlLog.setConfigId(config.getId());
        crawlLogService.insert(crawlLog);

        // Run Python script asynchronously
        runCrawlScript(config, crawlLog.getId());

        Map<String, Object> result = new HashMap<>();
        result.put("message", "Crawl triggered successfully");
        result.put("logId", crawlLog.getId());
        return AjaxResult.success(result);
    }

    /**
     * Trigger all enabled crawl configs.
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:edit')")
    @PostMapping("/triggerAll")
    public AjaxResult triggerAll() {
        CrawlConfig query = new CrawlConfig();
        query.setEnabled(1);
        List<CrawlConfig> enabledConfigs = crawlConfigService.selectList(query);

        if (enabledConfigs == null || enabledConfigs.isEmpty()) {
            return AjaxResult.error("No enabled crawl configs found");
        }

        int triggered = 0;
        for (CrawlConfig config : enabledConfigs) {
            CrawlLog crawlLog = new CrawlLog();
            crawlLog.setSiteName(config.getSiteName());
            crawlLog.setKeyword(config.getKeyword());
            crawlLog.setStatus("running");
            crawlLog.setStartTime(new Date());
            crawlLog.setConfigId(config.getId());
            crawlLogService.insert(crawlLog);

            runCrawlScript(config, crawlLog.getId());
            triggered++;
        }

        return AjaxResult.success("Triggered " + triggered + " crawls");
    }

    /**
     * Run the crawl Python script as a subprocess asynchronously.
     * The script is responsible for updating the crawl_log entry with status, items_found, items_saved, and end_time.
     */
    private void runCrawlScript(CrawlConfig config, Long logId) {
        String scriptFile = getSiteScript(config.getSiteName());
        if (scriptFile == null) {
            log.warn("No script mapping for site: {}", config.getSiteName());
            return;
        }

        String scriptPath = "/root/workspace/RuoYi-backend/crawlers/" + scriptFile;

        // Escape keyword for shell
        String escapedKeyword = config.getKeyword().replace("\"", "\\\"");
        String command = String.format(
            "python3 %s --config-id %d --keyword \"%s\" --max %d --log-id %d",
            scriptPath, config.getId(), escapedKeyword,
            config.getMaxResults() != null ? config.getMaxResults() : 10,
            logId
        );

        new Thread(() -> {
            try {
                ProcessBuilder pb = new ProcessBuilder("bash", "-c", command);
                pb.redirectErrorStream(true);
                Process process = pb.start();
                process.waitFor();
                log.info("Crawl script finished for config {}, exit code: {}", config.getId(), process.exitValue());
            } catch (Exception e) {
                log.error("Failed to run crawl script for config {}: {}", config.getId(), e.getMessage());
                // Update log to failed on script execution error
                CrawlLog failedLog = new CrawlLog();
                failedLog.setId(logId);
                failedLog.setStatus("failed");
                failedLog.setErrorMsg("Script execution error: " + e.getMessage());
                failedLog.setEndTime(new Date());
                crawlLogService.update(failedLog);
            }
        }, "crawl-exec-" + config.getId()).start();
    }

    /**
     * Map site name to Python spider script filename.
     */
    private String getSiteScript(String siteName) {
        if (siteName == null) return null;
        switch (siteName.toLowerCase().trim()) {
            case "bluesky": return "bluesky_spider.py";
            case "cnn": return "cnn_spider.py";
            case "u.s. treasury":
            case "us treasury":
            case "treasury": return "treasury_spider.py";
            default: return siteName.toLowerCase().trim().replace(" ", "_") + "_spider.py";
        }
    }
}
