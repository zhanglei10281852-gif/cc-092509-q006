# 专利与技术秘密档案管理服务

这是一个面向研发机构、法务部门和保密办公室的模块化后端，集中管理专利交底资料、技术秘密载体、移交批次、受控副本签发、查阅借阅、对外披露、归还、合规处置、载体盘点、版本与载体来源、密级库位、泄密事件、登录权限、审计以及可恢复后台任务。项目使用 FastAPI 与 SQLite，所有运行数据保存在单个本地数据库文件中，不依赖另行部署的数据库、缓存或消息队列。

## 已有能力

- 身份与权限：支持引导管理员、登录、会话、用户、角色和细粒度权限。
- 批次与二维码：移交批次保存项目、数量和稳定二维码载荷。
- 档案登记：登记专利交底、工艺文档、源代码介质等资产，保存密级库位和生命周期状态。
- 受控副本签发：一次事务内扣减来源载体、创建副本、记录损耗和版本来源事件。
- 查阅借阅归还：保存查阅用途、到期时间、部分归还和最终归还状态。
- 对外披露登记：使用幂等键登记合作方、披露范围和载体消耗，防止重复请求二次扣减。
- 位置脱敏：普通权限只能看到受限库位的替代码，授权人员可查看精确位置。
- 双人审批：合规处置、敏感库位解密等高风险操作要求申请人与审批人分离，并累计不同审批人的决定。
- 泄密事件追踪：事件可以关联档案或移交批次，保存严重度、调查状态和处置结果。
- 新颖性证据包：证据节点按来源自然键与幂等键去重，重复导入不产生重复节点、不能替换原文；包内条目按来源登记顺序形成哈希链，记录内容摘要与引用范围，提交后只能追加补充材料。
- 异议与裁决留痕：异议可针对条目或整个证据包，裁决支持成立、补正（须引用补充条目）、驳回，裁决人与提出人必须分离，处理结果全程可追溯。
- 证据跨交底/家族复用：同一证据可绑定到多个交底书或专利家族，各自保存独立解释；提交、异议、裁决、归档均写入包事件流与审计。
- 证据链校验：HTTP 接口与 `verify-evidence` 离线命令共用同一套校验，明确报告断链、序号断档、哈希链断裂、原文/摘要不匹配等问题。
- 审计与任务：关键身份及业务操作留痕，后台任务支持去重、领取与完成。

## 运行环境

- Python 3.11
- SQLite 3，由 Python 标准库提供
- Linux、macOS 或 Windows

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

默认数据库位于 `./data/archives.db`，可用 `ARCHIVE_DATABASE_PATH` 指定其他路径。

## 初始化与完整性检查

```bash
python -m app.cli init-db
python -m app.cli check-db
python -m app.cli verify-evidence   # 证据链完整性离线校验，发现问题时以非零码退出
```

## 启动 API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 新颖性证据包接口

所有接口位于 `/api/evidence` 下，需要会话令牌与相应权限（`evidence.read/write/challenge/decide/archive/verify`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/items` | 登记/导入证据；相同来源键或幂等键重复导入返回既有节点（`replayed=true`），内容变化返回 409 而不替换原文 |
| GET | `/items`、`/items/{id}` | 列出/查看证据节点 |
| POST | `/packages` | 创建证据包（面向交底书或专利家族） |
| GET | `/packages`、`/packages/{id}` | 列表/详情（含有序条目链、异议、复用绑定、事件流） |
| POST | `/packages/{id}/entries` | 追加条目（内容摘要、引用范围入哈希链；提交后仍可追加 `supplement`） |
| POST | `/packages/{id}/submit` | 提交证据包（空包不能提交） |
| POST | `/packages/{id}/archive` | 归档（须先提交且无未裁决异议；归档后冻结并生成归档摘要） |
| POST | `/packages/{id}/challenges` | 提出异议（针对条目或整包） |
| POST | `/challenges/{id}/decision` | 裁决异议：`upheld`/`amended`/`rejected`，裁决人不得是提出人 |
| POST | `/challenges/{id}/withdraw` | 撤回异议 |
| POST | `/packages/{id}/bindings` | 把同一证据复用到另一交底/家族并保存独立解释 |
| PATCH | `/bindings/{id}`、GET `/bindings?subject_kind=&subject_ref=` | 维护/按对象查看复用解释 |
| GET | `/verification` | 在线校验证据链；存在断链或摘要不匹配时返回 422 并在 `findings` 逐条列明 |

## 测试

```bash
python -m pytest
```

## 编译检查

```bash
python -m compileall -q app tests
```

## API 冒烟

```bash
python -m app.cli smoke
```
