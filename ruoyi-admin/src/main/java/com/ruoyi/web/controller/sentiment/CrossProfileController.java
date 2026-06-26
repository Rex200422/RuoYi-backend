package com.ruoyi.web.controller.sentiment;

import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.system.domain.sentiment.HighFrequencyUser;
import com.ruoyi.system.domain.sentiment.UserCrossProfile;
import com.ruoyi.system.service.sentiment.IHighFrequencyUserService;
import com.ruoyi.system.service.sentiment.IUserCrossProfileService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import java.util.List;
import java.util.Map;
import java.util.HashMap;

@RestController
@RequestMapping("/system/sentiment/crossProfile")
public class CrossProfileController {

    @Autowired private IHighFrequencyUserService hfUserService;
    @Autowired private IUserCrossProfileService profileService;

    /** 查看高频用户 */
    @GetMapping("/highFreq")
    public AjaxResult getHighFreqUsers(
            @RequestParam(defaultValue = "50") int limit) {
        List<HighFrequencyUser> list = hfUserService.selectTopByPlatform(limit);
        return AjaxResult.success(list);
    }

    /** 查看跨平台画像结果 */
    @GetMapping("/result")
    public AjaxResult getResults(
            @RequestParam(defaultValue = "20") int limit,
            @RequestParam(defaultValue = "0") int page) {
        List<UserCrossProfile> list = profileService.selectByPage(limit, page * limit);
        int total = profileService.countAll();
        Map<String, Object> result = new HashMap<>();
        result.put("list", list);
        result.put("total", total);
        return AjaxResult.success(result);
    }

    /** 查看单个用户的画像 */
    @GetMapping("/result/{username}")
    public AjaxResult getResultByUsername(@PathVariable String username) {
        UserCrossProfile profile = profileService.selectByUsername(username);
        if (profile == null) {
            return AjaxResult.error("未找到该用户的画像数据");
        }
        return AjaxResult.success(profile);
    }
}
