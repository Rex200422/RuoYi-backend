package com.ruoyi.web.controller.sentiment;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.NewsArticle;
import com.ruoyi.system.service.sentiment.INewsArticleService;

@RestController @RequestMapping("/system/sentiment/news")
public class NewsArticleController extends BaseController {
    @Autowired private INewsArticleService svc;
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list") public TableDataInfo list(NewsArticle q) { startPage(); return getDataTable(svc.selectList(q)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{id}") public AjaxResult getInfo(@PathVariable Long id) { return success(svc.selectById(id)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{ids}") public AjaxResult remove(@PathVariable Long[] ids) { return toAjax(svc.deleteByIds(ids)); }
}
