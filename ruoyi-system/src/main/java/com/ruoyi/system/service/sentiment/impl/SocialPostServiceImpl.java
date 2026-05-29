package com.ruoyi.system.service.sentiment.impl;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.SocialPost;
import com.ruoyi.system.mapper.sentiment.SocialPostMapper;
import com.ruoyi.system.service.sentiment.ISocialPostService;
@Service
public class SocialPostServiceImpl implements ISocialPostService {
    @Autowired private SocialPostMapper m;
    public List<SocialPost> selectList(SocialPost q) { return m.selectList(q); }
    public SocialPost selectById(String uuid) { return m.selectById(uuid); }
    public int deleteByUuids(String[] uuids) { return m.deleteByUuids(uuids); }
}
