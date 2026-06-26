package com.ruoyi.system.service.sentiment;

import com.ruoyi.system.domain.sentiment.UserCrossProfile;
import java.util.List;

public interface IUserCrossProfileService {
    int insert(UserCrossProfile profile);
    List<UserCrossProfile> selectByPage(int limit, int offset);
    UserCrossProfile selectByUsername(String username);
    int countAll();
}
