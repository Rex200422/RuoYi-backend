package com.ruoyi.web.controller.sentiment;

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

    @Autowired private IAiSummaryService svc;
    @Autowired private AiSummaryGenerator generator;

    /**
     * 查询AI简报分页列表
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list")
    public TableDataInfo list(AiSummary q) {
        startPage();
        return getDataTable(svc.selectList(q));
    }

    /**
     * 获取最新一条AI简报
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/latest")
    public AjaxResult latest() {
        return success(svc.selectLatest());
    }

    /**
     * 根据ID获取AI简报详情
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{id}")
    public AjaxResult getInfo(@PathVariable Long id) {
        return success(svc.selectById(id));
    }

    /**
     * 新增AI简报
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:add')")
    @PostMapping
    public AjaxResult add(@RequestBody AiSummary s) {
        return toAjax(svc.insert(s));
    }

    /**
     * 删除AI简报
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids) {
        return toAjax(svc.deleteByIds(ids));
    }

    /**
     * 手动触发AI简报生成（测试用）
     */
    @GetMapping("/test-generate")
    public AjaxResult testGenerate() {
        try {
            generator.generate(1);
            return success("简报生成完成");
        } catch (Exception e) {
            return AjaxResult.error("生成失败: " + e.getMessage());
        }
    }

}