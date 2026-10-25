package com.cosmos.api.market.controller;

import com.cosmos.api.market.dto.MarketIndexListResponse;
import com.cosmos.api.market.service.MarketService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/market")
public class MarketController {

    private final MarketService marketService;

    public MarketController(MarketService marketService) {
        this.marketService = marketService;
    }

    /** 코스피·코스닥 지수. period 1M·3M·6M·1Y (거래일 20·60·120·250개 + 기준점 1개) */
    @GetMapping("/indices")
    public MarketIndexListResponse findIndices(@RequestParam(defaultValue = "3M") String period) {
        return marketService.findIndices(period);
    }
}
