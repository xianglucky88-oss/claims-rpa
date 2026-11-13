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

## 只读门户查询

`BrowserAdapter` 可在构造时注入受控本地门户 `LocalPortal`（`fixtures/local_portal.json`），
此时只执行登录、搜索、轮询和读取结果页，不提交、不修改案件：

```bash
PYTHONPATH=src python3 -m claims_rpa.cli --fixture fixtures/portal_cases.json \
  --database /tmp/claims-portal.sqlite3 --portal-fixture fixtures/local_portal.json
```

每次查询都会生成新的请求令牌，结果必须同时绑定平台、请求案件与本次搜索；页面显示的
平台/案件号、会话代次与结构证据不足时不会写入 `cases`。连续查询复用同一平台的会话，
过期时只允许一次重新登录（新代次作废旧渲染）；身份不符、旧结果晚到或页面长期不刷新会
清理该页面上下文，使下一张案件从干净状态开始查询。

失败分类写入 `failures.category`：`not_found`、`auth`、`structure`、`identity`、
`stale`、`timeout`、`validation`、`provenance`。单案失败不会中断批次。

`cases` 与 `failures` 同时记录 `source`、`verified`、`request_token`、`page_platform`、
`page_case_id`、`session_epoch`，因此每次数据库变更都能追溯到产生它的页面身份。
dry-run 的确定性模拟值标记为 `source=simulated, verified=0`，普通查询不会把模拟
`approved` 当成真实门户确认（`ProvenanceError`）。
