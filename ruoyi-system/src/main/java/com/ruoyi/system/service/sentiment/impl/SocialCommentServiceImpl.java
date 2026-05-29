package com.ruoyi.system.service.sentiment.impl;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.SocialComment;
import com.ruoyi.system.mapper.sentiment.SocialCommentMapper;
import com.ruoyi.system.service.sentiment.ISocialCommentService;
@Service
public class SocialCommentServiceImpl implements ISocialCommentService {
    @Autowired private SocialCommentMapper m;
    public List<SocialComment> selectByPostId(String postId) { return m.selectByPostId(postId); }
    public List<SocialComment> selectList(SocialComment q) { return m.selectList(q); }
}
