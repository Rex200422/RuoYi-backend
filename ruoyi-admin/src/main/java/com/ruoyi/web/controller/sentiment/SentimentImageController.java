package com.ruoyi.web.controller.sentiment;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;
import com.ruoyi.common.core.domain.AjaxResult;
import java.io.File;
import java.util.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;

@RestController
@RequestMapping("/system/sentiment/image")
public class SentimentImageController {
    @Value("${ruoyi.profile:/home/ruoyi/uploadPath}")
    private String uploadPath;
    
    @Autowired
    private JdbcTemplate jdbcTemplate;
    
    /** 获取帖子的所有图片 */
    @GetMapping("/list")
    public AjaxResult listByPost(@RequestParam("postId") String postId) {
        List<Map<String,Object>> images = jdbcTemplate.queryForList(
            "SELECT id, image_url, local_path, idx FROM social_post_image WHERE post_id=? ORDER BY idx", postId);
        return AjaxResult.success(images);
    }
    
    /** 提供图片访问 */
    @GetMapping("/file/{filename}")
    public ResponseEntity<FileSystemResource> getFile(@PathVariable String filename) {
        File file = new File(uploadPath + "/sentiment/images/" + filename);
        if (!file.exists()) {
            return ResponseEntity.notFound().build();
        }
        FileSystemResource resource = new FileSystemResource(file);
        return ResponseEntity.ok()
            .contentType(MediaType.IMAGE_JPEG)
            .header(HttpHeaders.CONTENT_DISPOSITION, "inline")
            .body(resource);
    }
}
