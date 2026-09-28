package com.cosmos.api.company.service;

import java.util.UUID;

record CompanyCursor(
        int searchRank,
        String companyName,
        UUID companyId
) {
}
