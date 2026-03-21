# 任务清单：Manager 受控演进与发布治理

> 历史计划文档：其中涉及的独立 `worker` 调度与发布能力已不再适用于当前仓库。

## 1. 🔧 受控编码入口收敛
- [x] Manager runtime 直接注入 `software_delivery`，不再只靠 skill + bash 间接进入
- [x] 阻止 Manager 直接用 `write/edit` 修改仓库代码与技能文件
- [x] 编码/修复意图下继续阻止 Manager 用 `bash` 直接探测或修改仓库，强制转 `software_delivery`
- [x] 保留 `data/` 等运行态状态写入能力，避免误伤正常记忆/状态更新

## 2. 🔧 发布与 Rollout 显式化
- [x] 为 `software_delivery` 增加 `target_service` / `rollout` / `validate_only`
- [x] 支持 `worker` 定向 build + restart，本地自动发布与结果回写
- [x] 为 rollout 增加失败回滚/终止状态与审计日志

## 3. 🔧 Worker 调度增强
- [x] 在 worker 选择时纳入能力、负载、最近错误率、队列长度
- [x] 为派发增加优先级与更稳定的 auto-pick 策略
- [x] 在状态查询里展示更明确的 worker 负载与派发原因

## 4. 🔧 队列治理与观测
- [x] 为 running task 增加 lease/heartbeat 续租机制，减少纯超时恢复误判
- [x] 增加 dead-letter / relay retry / progress backlog 的观测摘要
- [x] 增加任务耗时、派发延迟、完成率等基础统计

## 5. 🔧 Skill 治理契约
- [x] 为 skill 增加 manifest/schema，声明运行目标、依赖、权限和 rollout 能力
- [x] 区分 `learned` / `builtin` / `worker-kernel` 等可修改级别
- [x] 将 skill 权限、自动发布和运行前检查统一收敛到契约层

## 6. ✅ 验证
- [x] 相关单测/回归测试通过
- [x] 手动验证 Manager 的编码请求会优先进入 `software_delivery`

## 7. 🔧 运行时解耦与动态化
- [x] Manager 提示词中的 direct tool 指南改为从 skill/tool 元数据动态生成
- [x] 权限分组支持从 `SKILL.md` frontmatter 读取 `policy_groups`，减少中心化技能分组硬编码
- [x] direct skill tool 执行改为 handler registry，移除 orchestrator 中的管理工具分支硬编码
- [x] rollout 目标从代码常量迁移到外部部署清单，避免服务名/镜像名写死
- [x] skill 平台 handler 注册改为显式 opt-in，默认不再自动挂载历史平台命令
- [x] 拆分 `manager/dev/service.py` 中的 skill 契约与 rollout 支撑逻辑，降低单文件复杂度
- [x] 补充对应测试与回归验证
