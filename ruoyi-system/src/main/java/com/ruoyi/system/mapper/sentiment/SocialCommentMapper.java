package com.ruoyi.system.mapper.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.SocialComment;
public interface SocialCommentMapper {
    List<SocialComment> selectByPostId(String postId);
    List<SocialComment> selectList(SocialComment q);
    int deleteByPostId(String postId);
}
