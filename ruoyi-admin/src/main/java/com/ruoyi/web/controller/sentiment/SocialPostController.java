package com.ruoyi.web.controller.sentiment;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.common.core.page.TableDataInfo;
import org.springframework.jdbc.core.JdbcTemplate;
import com.ruoyi.system.domain.sentiment.SocialPost;
import com.ruoyi.system.service.sentiment.ISocialPostService;

@RestController @RequestMapping("/system/sentiment/post")
public class SocialPostController extends BaseController {
    @Autowired private ISocialPostService svc;
    @Autowired private JdbcTemplate jdbc;
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/list") public TableDataInfo list(SocialPost q) { startPage(); return getDataTable(svc.selectList(q)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:query')")
    @GetMapping("/{uuid}") public AjaxResult getInfo(@PathVariable String uuid) { return success(svc.selectById(uuid)); }
    @PreAuthorize("@ss.hasPermi('system:sentiment:remove')")
    @DeleteMapping("/{uuids}") public AjaxResult remove(@PathVariable String[] uuids) { return toAjax(svc.deleteByUuids(uuids)); }

    /**
     * 获取Bluesky作者分布统计（用于词云图）
     */
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/authorStats")
    public AjaxResult authorStats(
            @RequestParam(defaultValue = "Bluesky") String siteName,
            @RequestParam(defaultValue = "50") int limit) {
        List<Map<String, Object>> rows = jdbc.queryForList(
            "SELECT author AS name, COUNT(*) AS value FROM social_post " +
            "WHERE site_name = ? AND author IS NOT NULL AND author != '' " +
            "GROUP BY author ORDER BY value DESC LIMIT ?",
            siteName, limit
        );
        return success(rows);
    }
}
