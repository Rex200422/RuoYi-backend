package com.ruoyi.web.controller.sentiment;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import com.ruoyi.system.domain.sentiment.SocialPost;
import com.ruoyi.system.service.sentiment.ISocialPostService;

@RestController @RequestMapping("/system/sentiment/post")
public class SocialPostController extends BaseController {
    @Autowired private ISocialPostService svc;
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list") public TableDataInfo list(SocialPost q) { startPage(); return getDataTable(svc.selectList(q)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{uuid}") public AjaxResult getInfo(@PathVariable String uuid) { return success(svc.selectById(uuid)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{uuids}") public AjaxResult remove(@PathVariable String[] uuids) { return toAjax(svc.deleteByUuids(uuids)); }
}
