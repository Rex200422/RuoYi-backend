package com.ruoyi.web.controller.sentiment;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.AiSummary;
import com.ruoyi.system.service.sentiment.IAiSummaryService;

@RestController
@RequestMapping("/system/sentiment/aiSummary")
public class AiSummaryController extends BaseController {

    private static final Logger log = LoggerFactory.getLogger(AiSummaryController.class);

    @Autowired private IAiSummaryService svc;
    @Autowired private AiSummaryGenerator generator;

    private final ExecutorService executor = Executors.newSingleThreadExecutor(r -> {
        Thread t = new Thread(r, "ai-summary-manual");
        t.setDaemon(true);
        return t;
    });
    private volatile boolean generating = false;

    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list")
    public TableDataInfo list(AiSummary q) {
        startPage();
        return getDataTable(svc.selectList(q));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/latest")
    public AjaxResult latest() {
        return success(svc.selectLatest());
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable Long id) {
        return success(svc.selectById(id));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:add')")
    @PostMapping
    public AjaxResult add(@RequestBody AiSummary s) {
        return toAjax(svc.insert(s));
    }

    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids) {
        return toAjax(svc.deleteByIds(ids));
    }

    /**
     * 异步立即生成简报。
     * 返回202表示已提交，前端轮询 /latest 获取结果。
     * 数据范围以当前时间为起点（而非半点）。
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @PostMapping("/generate")
    public AjaxResult generateNow() {
        if (generating) {
            return AjaxResult.error("当前有简报正在生成中，请稍后再试");
        }
        generating = true;
        log.info("[AI Summary] 用户手动触发简报生成...");
        CompletableFuture.runAsync(() -> {
            try {
                generator.generate(1);
            } catch (Exception e) {
                log.error("[AI Summary] 手动生成失败: {}", e.getMessage());
            } finally {
                generating = false;
            }
        }, executor);
        return AjaxResult.success("简报已提交生成，预计需要1-3分钟，完成后会自动显示");
    }

    /**
     * 检查是否正在生成中
     */
    @GetMapping("/generating")
    public AjaxResult isGenerating() {
        return success(generating);
    }
}
