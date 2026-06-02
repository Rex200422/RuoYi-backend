package com.ruoyi.web.controller.sentiment;

import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.domain.AjaxResult;

@RestController
@RequestMapping("/system/sentiment/platform")
public class SentimentPlatformController {

    @Autowired
    private JdbcTemplate jdbcTemplate;

    /** 获取所有启用的平台列表 */
    @GetMapping("/list")
    public AjaxResult list() {
        List<Map<String, Object>> platforms = jdbcTemplate.queryForList(
            "SELECT id, platform_name, category, display_name, icon FROM sentiment_platform WHERE enabled=1 ORDER BY category, id");
        return AjaxResult.success(platforms);
    }

    /** 按分类获取平台列表 */
    @GetMapping("/listByCategory")
    public AjaxResult listByCategory(@RequestParam("category") String category) {
        List<Map<String, Object>> platforms = jdbcTemplate.queryForList(
            "SELECT id, platform_name, display_name, icon FROM sentiment_platform WHERE category=? AND enabled=1 ORDER BY id", category);
        return AjaxResult.success(platforms);
    }
}
