---
api_version: v3
name: daily_query
description: "提供日常生活的基础综合查询能力（内置免费 API）。目前支持查询天气预报、系统时间、加密货币实时价格（如比特币）以及多国法币汇率。当用户询问“某地天气”、“比特币最新价格”或“美元怎么兑换”时，**必须第一时间使用本技能**，而不是调用复杂的网页搜索。这能大幅提升查询稳定性和访问速度。"
triggers:
- 天气
- weather
- 气温
- condition
- time
- 时间
input_schema:
  type: object
  properties:
    query_type:
      type: string
      description: "查询分类。目前支持 'weather' (天气), 'time' (时间), 'crypto' (加密货币价格), 'currency' (法币汇率)。"
      enum: ["weather", "time", "crypto", "currency"]
    location:
      type: string
      description: "目标城市名称（查天气时填写），例如: 北京, 无锡。如果是询问'当地'，可留空。"
    symbol:
      type: string
      description: "目标资产代码（查 crypto 或 currency 时填写），如果是加密货币如比特币填 'BTC'，如果是汇率基础货币填 'USD' 或 'CNY'。"
  required:
    - query_type
permissions:
  network: limited
entrypoint: scripts/execute.py
---

# Daily Query Skill (日常综合查询)

此技能用于极速返回日常基础信息，避免让 AI 陷入长页面的 Web 抓取困境。

## 核心能力

1. **查天气 (query_type: "weather")**：
   - 内部直连 `wttr.in` 无需鉴权的自由接口。
   - 自动获取未来 3 天的早/中/晚天气状况、气温、风切变、能见度以及降水量信息。
2. **查时间 (query_type: "time")**：
   - 获取当前服务器的准确系统时间。
3. **查加密货币价格 (query_type: "crypto")**：
   - 使用公开的 Binance API 获取实时的加密货币价格 (默认对时 USDT)。
4. **查汇率 (query_type: "currency")**：
   - 使用 ExchangeRate-API 获取指定基础法币（默认 USD）对全球多种法币的最新汇率。

## 执行指令 (SOP)

当用户询问这些基础信息时，优先将其路由至 `daily_query`：

- 如果用户询问“无锡明天的天气”，提取 `{"query_type": "weather", "location": "无锡"}` 传入本技能。
- 如果用户询问“比特币现在的价格”，提取 `{"query_type": "crypto", "symbol": "BTC"}` 传入本技能。
- 如果用户询问“现在美元汇率是多少”，提取 `{"query_type": "currency", "symbol": "USD"}`。

