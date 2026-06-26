package com.ruoyi.system.mapper.sentiment;

import com.ruoyi.system.domain.sentiment.HighFrequencyUser;
import org.apache.ibatis.annotations.Param;
import java.util.List;

public interface HighFrequencyUserMapper {
    int batchInsertOrUpdate(@Param("list") List<HighFrequencyUser> list);
    List<HighFrequencyUser> selectTopByPlatform(@Param("limit") int limit);
    int deleteByWindowStartBefore(@Param("time") java.util.Date time);
}
