package com.ruoyi.system.service.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.SocialPost;
public interface ISocialPostService {
    List<SocialPost> selectList(SocialPost q);
    SocialPost selectById(String uuid);
    int deleteByUuids(String[] uuids);
}
