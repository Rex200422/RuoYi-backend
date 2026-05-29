package com.ruoyi.web.controller.sentiment;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/system/sentiment/crawlLog")
public class CrawlLogController extends BaseController {

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
}
