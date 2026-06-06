package com.ruoyi.system.service.sentiment.impl;

import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.AiSummary;
import com.ruoyi.system.mapper.sentiment.AiSummaryMapper;
import com.ruoyi.system.service.sentiment.IAiSummaryService;

@Service
public class AiSummaryServiceImpl implements IAiSummaryService {
    @Autowired private AiSummaryMapper m;

    public List<AiSummary> selectList(AiSummary q) { return m.selectList(q); }
    public AiSummary selectById(Long id) { return m.selectById(id); }
    public long selectCount(AiSummary q) { return m.selectCount(q); }
    public int insert(AiSummary s) { return m.insert(s); }
    public int deleteByIds(Long[] ids) { return m.deleteByIds(ids); }
    public AiSummary selectLatest() { return m.selectLatest(); }
}
