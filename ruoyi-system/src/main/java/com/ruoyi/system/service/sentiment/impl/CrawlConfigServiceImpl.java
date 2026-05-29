package com.ruoyi.system.service.sentiment.impl;

import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.CrawlConfig;
import com.ruoyi.system.mapper.sentiment.CrawlConfigMapper;
import com.ruoyi.system.service.sentiment.ICrawlConfigService;

@Service
public class CrawlConfigServiceImpl implements ICrawlConfigService {
    @Autowired private CrawlConfigMapper m;

    public List<CrawlConfig> selectList(CrawlConfig q) { return m.selectList(q); }
    public CrawlConfig selectById(Long id) { return m.selectById(id); }
    public int insert(CrawlConfig config) { return m.insert(config); }
    public int update(CrawlConfig config) { return m.update(config); }
    public int deleteByIds(Long[] ids) { return m.deleteByIds(ids); }
    public List<CrawlConfig> selectDueConfigs() { return m.selectDueConfigs(); }
}
