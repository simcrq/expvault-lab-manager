# AI / MCP 标准接口

expvault 内置标准 **Model Context Protocol (MCP)** JSON-RPC 2.0 服务，无需额外第三方 MCP 依赖，即可直接让 AI 助手（Antigravity、Claude Desktop、Cursor、VS Code、Gemini CLI 等）读取与协作管理本机实验库和工艺条件矩阵。

## 启动方式

### 1. 标准 Stdio MCP 模式（推荐）

独立发布包：

```sh
python lab_manager.py --mcp --root "/path/to/实验库"
```

源码目录：

```sh
python expvault.py --root "/path/to/实验库" mcp
```

可在网页左侧栏点击「**AI / MCP 接口**」直接一键复制包含本机真实 Python 路径与实验库路径的 `mcpServers` JSON 配置：

```json
{
  "mcpServers": {
    "expvault": {
      "command": "python",
      "args": ["/path/to/lab_manager.py", "--mcp", "--root", "/path/to/实验库"]
    }
  }
}
```

### 2. 本机 HTTP JSON-RPC 2.0 端点

网页服务运行时，同时在 `127.0.0.1` 上开放本机端点 `POST /api/mcp`（需携带当前会话的 `Authorization: Bearer <token>` 与 `X-Expvault-Request: 1` 头），仅允许本机回环地址访问。

## 可用工具清单（19 项）

| 工具名 | 权限 | 说明 |
| --- | --- | --- |
| `vault_overview` | 只读 | 获取当前实验库统计、最近实验与活动记录 |
| `experiments_list` | 只读 | 按关键词、状态、分组和分页筛选实验 |
| `experiment_get` | 只读 | 读取实验完整元数据、工艺条件矩阵、测量与步骤日志 |
| `experiments_search` | 只读 | 全局搜索实验、参数、工艺条件与文件标签 |
| `files_list` | 只读 | 列出实验文件及其标签（`tags`）、大小、角色与 SHA256 |
| `file_read_text` | 只读 | 读取实验内 UTF-8 文本数据文件（CSV / TXT / JSON 等，最多 200 KB） |
| `conditions_list` | 只读 | 获取工艺条件矩阵、自动识别的差异/公共参数与 Markdown 表 |
| `conditions_export_table` | 只读 | 导出单实验工艺条件表为 `.xlsx` / `.csv` / `.md` 并返回文件路径与 Markdown |
| `experiment_verify` | 只读 | 执行 SHA256 文件完整性校验 |
| `experiment_create` | 读写 | 新建实验记录 |
| `experiment_update` | 读写 | 更新实验标题、状态、基准参数、结果、结论、备注与分组 |
| `condition_save` | 读写 | 新增或更新工艺条件，支持关联已有文件或导入本机图片/数据文件 |
| `conditions_batch_create` | 读写 | 单次批量创建 1–60 组工艺条件矩阵 |
| `condition_update_status` | 读写 | 修改工艺条件状态（`pending` / `tuning` / `ok` / `best` / `failed`） |
| `condition_delete` | 读写 | 删除工艺条件（磁盘原文件保留在文件资料中） |
| `step_add` | 读写 | 追加实验操作步骤（可选引用资料） |
| `note_append` | 读写 | 追加带时间戳日志（可选引用资料） |
| `measurement_upsert` | 读写 | 更新仪器与多轮对照测量（`measurement_groups`） |
| `files_set_tags` | 读写 | 设置或追加文件标签 |

## 可用资源（Resources）

- `expvault://overview`：实验库统计概览（JSON）
- `expvault://experiments`：活跃实验列表（JSON）
- `expvault://experiment/{exp_id}`：单个实验完整元数据（JSON）
- `expvault://experiment/{exp_id}/conditions`：指定实验的工艺条件 Markdown 对照表

## 并发与安全边界

- 所有写入工具均通过实验库内的 `.expvault/harness.lock` 与网页端 / 桌面 GUI 串行写入，并支持 `expected_revision` 并发冲突检查。
- 连接 NAS 只读库（`viewer` 模式）时，所有写入工具会被自动拒绝，仅允许查询与导出。
- 文件读写严格限制在实验目录边界内，拒绝路径穿越与符号链接。
