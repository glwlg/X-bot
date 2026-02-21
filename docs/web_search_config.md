# Web Search 服务配置指南

X-Bot 内置了聚合搜索引擎 `web_search` 技能。该技能采用了基于优先级的后撤轮询（Fallback）机制，确保可以在稳定、免费、高效之间取得最好的平衡。

## 优先级调度顺序
当前支持并按照以下顺序进行容灾重试请求：

1. **Tavily (优先考虑)** 或 **Exa (同级轮询)**
2. **DuckDuckGo**
3. **SearXNG (独立自建节点)**
4. **SearXNG (公共代理池节点)**

### 1. Tavily Provider
专注于大模型 AI Agent 优化的搜索引擎。若配置此项，会优先使用它进行搜索。
- **获取 API Key**: 前往 [Tavily](https://tavily.com/) 注册即可获得。
- **配置方式**: 在 `.env` 中添加 `TAVILY_API_KEY="tvly-..."`。

### 2. Exa Provider
与 Tavily 同级，专为神经网络语义搜索设计的强大 AI 检索引擎。
- **获取 API Key**: 前往 [Exa](https://exa.ai/) 注册即可获得。
- **配置方式**: 在 `.env` 中添加 `EXA_API_KEY="YOUR_API_KEY"`。
> Note: 如果你同时配置了 Tavily 和 Exa，系统会在它们之间进行**随机轮询**，将负载和免费额度平摊到两个引擎上。

### 3. DuckDuckGo Provider
当顶级 AI 搜索引擎额度耗尽或未配置时，会自动回退使用官方开源库 `duckduckgo-search`。
- **特性**: 完全免费，聚合性强。
- **配置方式**: 开箱即用，**无需额外配置**。
> 注意：高频调用可能会受到 DuckDuckGo 官方防爬虫 IP 的限制，但我们的降级机制会掩盖该故障。

### 4. SearXNG (独立自建节点)
若鸭鸭搜因为 IP 限制请求失败，系统将回拨到你的私有代理搜索引擎。这是最高自由度的长效稳定方案。
- **配置方式**: 在 `.env` 中写入 `SEARXNG_URL=http://<你部署的服务地址>:<端口>/search`
- **如何部署**: X-Bot 提供了一套独立的 Docker Compose 文件供你一键启动：
  ```bash
  cd deploy/searxng
  docker compose up -d
  ```

### 5. SearXNG (内置公共代理池)
最后一道防线。当所有的查询都遇到瓶颈时，技能会从内部精心挑选的一批 SearXNG 免费公益实例中，随机挑选节点发送请求，直至至少获取一份可用数据答复。
- **配置方式**: 开箱即用，无感知降级，绝不宕机。
