# 业务工作台生成器 · 跨平台版 v1.1.0

从生服工作台提炼出的通用业务框架。**业务指令、文件契约、计算脚本和界面模板共用，模型及 Agent 平台可以替换。**不依赖 Codex API、模型厂商 SDK、原作者数据库或原系统账号。

分享的 Skill 包只含代码、文档、空白模板和临时合成测试代码；不含旧业务数据库、客户数据、账号或凭据。每位使用者用自己的文件建立独立工作台。

## 三种接入方式

| 使用环境 | 怎么接入 | 能否实际自动建台 |
|---|---|---|
| 支持 Skill 目录的 Agent | 导入整个目录或原生目录 ZIP；可用通用安装器复制 | 宿主还需具备文件读写、Python 与持久目录 |
| 支持自定义工具的 Agent / 模型 API | 注册工具 schema，将请求送到 Python JSON 桥 | 接通宿主执行器后可以，不要求使用哪家模型 |
| 豆包、DeepSeek 等普通聊天界面 | 粘贴 AGENT_PROMPT.md，或按实际支持作为智能体指令/知识文件 | 根据该界面实际文件与执行能力判断；只有聊天能力时只能先做需求、映射、配置与执行方案 |

“通用”指框架和脚本可移植，不表示任何聊天网页上传一个 ZIP 就自动获得运行服务器和保存数据库的能力。

## 方式一：目录技能

对支持 SKILL.md 的宿主，解压后把 `service-workbench-builder` 放进该平台实际扫描的技能目录，保持资源相对位置。

也可以在解压后的目录中运行通用安装器：

```sh
python3 scripts/install.py --skills-dir "<目标平台的技能根目录>" --dry-run
python3 scripts/install.py --skills-dir "<目标平台的技能根目录>"
```

目标已存在时不会覆盖。安装器只复制目录，不会修改模型配置或授予工具权限。Windows 可按本机环境使用 python 或 py -3 代替 python3。

已查证的方向包括 DeepSeek Harness 的文件技能加载机制、方舟 Managed Agents 的 Skills 导入，以及 Codex 原生加载。对应官方机制、预览/账户条件和链接见 [references/platforms.md](../references/platforms.md)。这些平台未在本次交付中逐一登录实测。

Codex 保持兼容：默认技能目录为 `~/.codex/skills/`，自定义 CODEX_HOME 时使用其 skills 子目录。[agents/openai.yaml](../agents/openai.yaml) 是可选 Codex UI 元数据，其他平台可忽略。

## 方式二：API / 自定义 Agent

提供：

- [adapters/tool.schema.json](../adapters/tool.schema.json)：模型可读的函数参数定义。
- [scripts/agent_bridge.py](../scripts/agent_bridge.py)：读取 JSON、调用同一业务引擎、输出 JSON 的执行器。
- [AGENT_PROMPT.md](../AGENT_PROMPT.md)：合并后的业务工作流与数据规则。

工具支持环境检查、空模板初始化、文件画像、字段配置、生成、刷新、指标核对和私有应用下载包。脚本不调用模型 API，不需要模型 Key；模型请求和计费由使用者自己的宿主管理。

宿主将上传文件放入每个用户的独立工作目录，再运行：

```sh
python3 scripts/agent_bridge.py --workspace-root "<该用户的工作目录>"
```

通过 stdin 提供一个 JSON，例如 `{"action":"capabilities"}` 或 `{"action":"init","output":"project","mode":"project"}`。宿主读取 stdout 的 ok/result/error 并回填模型；完整动作与消息信封适配说明见 [references/platforms.md](../references/platforms.md)。

支持工具调用的 DeepSeek / 豆包模型可以接到这种执行器；厂商之间的 API 消息格式仍需按各自文档包装，不把 schema 上传当作已经接通脚本。

## 方式三：普通聊天 / 智能体指令

打开 [AGENT_PROMPT.md](../AGENT_PROMPT.md)，将全文粘贴到新对话，或按平台支持设置为智能体指令/知识附件。这个文件已合并所需规则，不要求继续打开本机相对路径。

然后说：

```text
按“业务工作台生成器”流程，帮我建立课程交付工作台。
先确认我要解决的问题和现有文件，再告诉我需要补什么。
如果你能运行 Python，请使用随附脚本实际校验并生成；
如果不能，请给出字段映射、配置和执行步骤，并明确哪些尚未运行。
```

如果平台能够解压且执行附件，可再提供完整 Skill 包；若只能读文字，不必让用户反复上传无法执行的代码。需要完整自动建台时，接入有执行能力的 Agent 宿主或由本机运行通用脚本。

## 通用业务流程

确认用途 → 提供现有文件或领取模板 → 核对字段、主键、日期与指标口径 → 校验与生成 → 使用工作台 → 后续刷新。

支持服务履约、项目交付、运营跟进、经营分析四类起点；现有中文表头可以映射，无需强制改成标准列名。

| 空白模板 | 内容 | 按需提供 |
|---|---|---|
| objects.csv | 客户、项目、门店、设备等业务对象 | 对象档案/跨表关联 |
| tasks.csv | 任务、负责人、期限、计划数量 | 任务/交付/履约 |
| executions.csv | 执行、验收、数量和证据引用 | 按验收量判断履约 |
| transactions.csv | 收支、金额、币种与实际/预估口径 | 经营收支 |

模板在 [assets/input-templates/](../assets/input-templates/)，全部只有表头；没有文件时可先生成空工作台。字段填写与统计规则见 [references/data-contract.md](../references/data-contract.md)。

## 运行与交付

Python 3.10+、SQLite、业务时区数据和文件读写能力是执行条件。本包不自带 Python；运行环境可为用户电脑或 Agent 分配的沙箱。

生成应用支持任务筛选、验收/到期判定、按币种收支、跟进备注保存、审计、CSV 导出、导入批次和刷新前备份。更复杂行业模型按用户需求扩展。

在远程沙箱生成时，127.0.0.1 地址属于沙箱，不能直接当用户访问链接。应交付宿主可下载的完整应用，或使用已经配置的实际预览/部署入口。`package` 工具生成的应用 ZIP **含当前用户数据**，与公开分享的空 Skill ZIP 分开保管。

默认应用为单人模式；团队身份/权限、云部署、持续同步需按实际宿主实现。refresh 是所有来源的完整快照替换，增量文件需先明确合并规则。

## 包内入口

- SKILL.md：有技能加载器时使用。
- AGENT_PROMPT.md：普通指令/知识附件/系统提示词入口。
- scripts/install.py：指定目标目录的通用安装器。
- scripts/agent_bridge.py：JSON 工具执行入口。
- scripts/workbench.py：直接调用的通用业务 CLI。
- adapters/tool.schema.json：跨模型函数参数协议。
- references/、assets/、tests/：业务规则、模板界面与合成回归验证。
