package com.ruoyi.system.service.sentiment.impl;

import com.ruoyi.system.domain.sentiment.HighFrequencyUser;
import com.ruoyi.system.mapper.sentiment.HighFrequencyUserMapper;
import com.ruoyi.system.service.sentiment.IHighFrequencyUserService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import java.util.Date;
import java.util.List;

@Service
public class HighFrequencyUserServiceImpl implements IHighFrequencyUserService {
    @Autowired private HighFrequencyUserMapper mapper;

    @Override
    public int batchInsertOrUpdate(List<HighFrequencyUser> list) {
        if (list == null || list.isEmpty()) return 0;
        return mapper.batchInsertOrUpdate(list);
    }

    @Override
    public List<HighFrequencyUser> selectTopByPlatform(int limit) {
        return mapper.selectTopByPlatform(limit);
    }

    @Override
    public int deleteByWindowStartBefore(Date time) {
        return mapper.deleteByWindowStartBefore(time);
    }
}
