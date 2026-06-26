package com.ruoyi.system.service.sentiment.impl;

import com.ruoyi.system.domain.sentiment.UserCrossProfile;
import com.ruoyi.system.mapper.sentiment.UserCrossProfileMapper;
import com.ruoyi.system.service.sentiment.IUserCrossProfileService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import java.util.List;

@Service
public class UserCrossProfileServiceImpl implements IUserCrossProfileService {
    @Autowired private UserCrossProfileMapper mapper;

    @Override
    public int insert(UserCrossProfile profile) { return mapper.insert(profile); }

    @Override
    public List<UserCrossProfile> selectByPage(int limit, int offset) {
        return mapper.selectByPage(limit, offset);
    }

    @Override
    public UserCrossProfile selectByUsername(String username) {
        return mapper.selectByUsername(username);
    }

    @Override
    public int countAll() { return mapper.countAll(); }
}
