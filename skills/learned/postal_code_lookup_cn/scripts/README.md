# 中国邮政编码查询技能（postal_code_lookup_cn）

## 功能
- 地址/地区 -> 6位邮编
- 6位邮编 -> 对应地区信息（若可解析）
- 多候选时返回前3条
- 网络失败/无结果时给出清晰提示与补充建议

## 实现说明
- 优先使用公开网页：youbianku.com 搜索页
- 解析库：`requests + BeautifulSoup`（可用时）
- 回退方案：标准库 `urllib + html.parser`
- 不保存查询历史，不写入个人隐私数据

## 参数
- `action`: 固定 `lookup`（默认）
- `query`: 地址文本或6位邮编
- `max_results`: 返回条数，默认3，最大3

## 用法示例
1. `{"action":"lookup","query":"无锡滨湖区"}`
2. `{"action":"lookup","query":"100080"}`
3. `{"action":"lookup","query":"北京 海淀 中关村","max_results":3}`

## 输出格式
- 查询内容
- 结果列表（地区 / 邮编）
- 数据来源说明（邮编库公开页面）

## 测试
- 运行示例测试（至少2个）：
  - `python -m unittest test_execute_examples.py`
