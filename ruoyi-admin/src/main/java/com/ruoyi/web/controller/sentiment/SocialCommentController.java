package com.ruoyi.web.controller.sentiment;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.controller.BaseController;
import com.ruoyi.common.core.domain.AjaxResult;
import com.ruoyi.system.domain.sentiment.SocialComment;
import com.ruoyi.system.service.sentiment.ISocialCommentService;

@RestController @RequestMapping("/system/sentiment/comment")
public class SocialCommentController extends BaseController {
    @Autowired private ISocialCommentService svc;
    @PreAuthorize("@ss.hasPermi('system:sentiment:list')")
    @GetMapping("/post") public AjaxResult listByPost(@RequestParam("postId") String postId) {
        return success(svc.selectByPostId(postId));
    }
}
