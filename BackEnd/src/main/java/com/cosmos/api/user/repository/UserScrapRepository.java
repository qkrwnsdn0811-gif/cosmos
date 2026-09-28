package com.cosmos.api.user.repository;

import com.cosmos.api.user.entity.UserScrap;
import com.cosmos.api.user.entity.UserScrapId;
import org.springframework.data.jpa.repository.JpaRepository;

public interface UserScrapRepository extends JpaRepository<UserScrap, UserScrapId> {
}
