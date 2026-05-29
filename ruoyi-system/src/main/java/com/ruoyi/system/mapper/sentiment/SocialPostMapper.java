package com.ruoyi.system.mapper.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.SocialPost;
public interface SocialPostMapper {
    List<SocialPost> selectList(SocialPost q);
    SocialPost selectById(String uuid);
    long selectCount(SocialPost q);
    int deleteByUuid(String uuid);
    int deleteByUuids(String[] uuids);
}
