package com.ruoyi.system.service.sentiment;

import com.ruoyi.system.domain.sentiment.HighFrequencyUser;
import java.util.List;

public interface IHighFrequencyUserService {
    int batchInsertOrUpdate(List<HighFrequencyUser> list);
    List<HighFrequencyUser> selectTopByPlatform(int limit);
    int deleteByWindowStartBefore(java.util.Date time);
}
