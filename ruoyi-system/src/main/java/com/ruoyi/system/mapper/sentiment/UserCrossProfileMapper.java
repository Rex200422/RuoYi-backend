package com.ruoyi.system.mapper.sentiment;

import com.ruoyi.system.domain.sentiment.UserCrossProfile;
import org.apache.ibatis.annotations.Param;
import java.util.List;

public interface UserCrossProfileMapper {
    int insert(UserCrossProfile profile);
    List<UserCrossProfile> selectByPage(@Param("limit") int limit, @Param("offset") int offset);
    UserCrossProfile selectByUsername(@Param("username") String username);
    int countAll();
}
