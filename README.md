# 医保理赔案件状态采集与数据入库 RPA

离线演示多平台案件查询、回执下载、结构化解析、幂等入库和失败分类。真实浏览器连接被抽象为 `BrowserAdapter`，默认使用 dry-run，不需要账号、连接串或 token。

## 数据工程链路

`案件队列 -> 浏览器任务调度 -> 页面/文件采集 -> 状态解析 -> 幂等去重 -> SQLite 入库 -> 回执与失败分类`。

## 外部依赖类型与版本

- Python 3.11+
- pytest 8.3.x（开发/测试）
- 浏览器自动化运行时：Playwright 1.48.x（可选，真实环境只注入配置，不写入代码）

## 运行

```bash
PYTHONPATH=src python3 -m claims_rpa.cli --fixture fixtures/cases.json --database /tmp/claims-rpa.sqlite3 --dry-run
PYTHONPATH=src python3 -m pytest tests
```
