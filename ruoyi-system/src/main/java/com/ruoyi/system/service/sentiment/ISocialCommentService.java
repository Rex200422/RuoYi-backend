package com.ruoyi.system.service.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.SocialComment;
public interface ISocialCommentService {
    List<SocialComment> selectByPostId(String postId);
    List<SocialComment> selectList(SocialComment q);
}
