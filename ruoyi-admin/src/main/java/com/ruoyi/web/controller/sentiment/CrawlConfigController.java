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
    @Autowired private CrawlScheduler crawlScheduler;
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

        // 检查是否已有 pending 或 running 任务
        int pendingOrRunning = crawlLogService.selectPendingOrRunningCountByConfigId(config.getId());
        if (pendingOrRunning > 0) {
            return AjaxResult.error("该平台已有正在运行或等待中的任务");
        }

        // 通过 CrawlScheduler 调度（受信号量限制，正确处理 pending 状态）
        crawlScheduler.triggerSingleCrawl(config);

        return AjaxResult.success("已触发 " + config.getSiteName() + " 爬取任务");
    }

    /**
     * Trigger all enabled crawl configs.
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:edit')")
    @PostMapping("/triggerAll")
    public AjaxResult triggerAll() {
        // 通过 CrawlScheduler 统一调度，受 semaphore 限制（MAX_CONCURRENT=2）
        int triggered = crawlScheduler.triggerAllEnabled();
        return AjaxResult.success("已触发 " + triggered + " 个爬取任务（并发受" + crawlScheduler.getMaxConcurrent() + "个限制）");
    }


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
