# 跨平台接入

本包分离“业务指令”“确定性脚本”“模型/宿主接入”。模型品牌不决定脚本能否运行，实际能力来自加载它的 Agent、沙箱或应用后端。一个模型可以接在多个不同宿主上。

## 按能力选择方式

| 实际环境 | 提供的入口 | 能做到什么 |
|---|---|---|
| 支持 SKILL.md，且有文件与 Python 工具 | 整个 Skill 目录/目录 ZIP | 读取需求和文件，执行导入、生成、核对、打包；是否可预览取决于宿主 |
| 自定义 Agent，支持工具调用 | AGENT_PROMPT.md + adapters/tool.schema.json + scripts/agent_bridge.py | 模型选择动作，宿主执行同一套脚本并回传结构化结果 |
| 只有代码执行沙箱，没有 Skill 加载器 | AGENT_PROMPT.md + 解压后的 scripts/assets/references | 由 Agent 调用 CLI 或 JSON 桥；交付可下载应用，不能假定沙箱地址对用户开放 |
| 普通聊天网页，能读文字/附件 | 粘贴 AGENT_PROMPT.md 或作为知识文件提供 | 引导需求、给模板和配置；仅当实际具备文件与执行工具时才继续真实生成 |

没有代码执行时，不能靠提示词获得后台数据库或保存功能。不要使用“任何平台一键安装并全自动运行”的无条件承诺。通用的是业务框架、输入契约、脚本和工具协议。

## 已查证的接入方向

以下仅记录 2026-10-26 查阅的官方机制，不代表本包已经登录或在这些产品里完成端到端运行。平台配置更新时以对应官方文档为准。

- **DeepSeek Harness**：官方文件技能提供器支持 `<name>/SKILL.md` 目录。启用相应提供器后，可将本目录放入项目 `.agents/skills/` 或其配置的技能根目录。Harness 属于独立 Agent 宿主，与普通聊天网页分开；版本/预览状态应以官方为准。[官方技能格式与发现规则](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md)
- **DeepSeek API**：支持工具调用；模型给出工具参数，宿主执行函数并回填结果。可注册同包工具 schema 并接到 Python 桥，不需要修改业务引擎。[官方工具调用说明](https://api-docs.deepseek.com/guides/tool_calls/)
- **豆包模型 / 火山方舟**：可通过模型工具调用接入执行器；Managed Agents 的 Skills/沙箱能力是另一条接入方向，需账户与实际环境支持。原生 Skills 导入使用只有一个顶层技能目录的 ZIP，平台返回的技能标识由该平台管理。[方舟工具调用](https://console.volcengine.com/ark/region%3Acn-beijing/docs/responses-api-tool-calling)、[Managed Agents Skills](https://docs.volcengine.com/docs/ark/skills?lang=en)
- **豆包工作**：开放平台列有 Skill/Plugin 接入，开放范围和入驻要求以当前账户与官方入口为准，不将其等同于普通聊天页的文件上传。[豆包开放平台](https://open.doubao.com/)
- **Codex**：本目录原生保留兼容，`agents/openai.yaml` 为可选 UI 适配。其他平台不需要读这个文件；核心脚本不导入 Codex/OpenAI SDK。

## 通用目录安装脚本

对已经支持文件技能目录的宿主，先确认其实际扫描的技能根目录，再运行：

```sh
python3 scripts/install.py --skills-dir "<该宿主的技能根目录>" --dry-run
python3 scripts/install.py --skills-dir "<该宿主的技能根目录>"
```

脚本把整个 `service-workbench-builder` 复制进去，保留所有资源相对位置；目标已存在就停止，不覆盖其他技能。安装器不会创建平台账号、修改模型配置或为聊天网页添加代码执行能力。只需知识/提示词的界面直接使用 AGENT_PROMPT.md，不运行目录安装器。

## 无平台 API 的直接运行

任何具备 Python 3.10+、SQLite、所用业务时区数据及文件读写能力的可信环境，都可解压后运行同一 CLI。无需模型 API Key，也不会自动调用任何付费模型。命令中的 python3 可替换为宿主实际的 Python 解释器；Windows 常见为 python 或 py -3，先按环境确认。

```sh
python3 scripts/workbench.py init --output ./my-project --mode project
python3 scripts/workbench.py profile ./my-project/inputs/tasks.csv
python3 scripts/workbench.py build --config ./my-project/blueprint.json --output ./my-project/app
python3 scripts/workbench.py serve --workspace ./my-project/app
```

上例空模板只能生成空工作台。导入真实文件前需按契约配置来源与口径。Python/时区缺失时先解决环境，不能把本包说成自带运行时。Windows、移动端与厂商云沙箱未在本次交付中实机验证。

## 工具调用接入

`adapters/tool.schema.json` 是通用的 Chat Completions 风格 function schema，不含模型名、URL、密钥或特定 SDK。宿主根据所用 API 包装字段；例如某些 Responses API 要把 function 字段展开到顶层。不要把不同 API 的消息信封当作完全相同。

宿主负责：用户身份和工作目录隔离、上传文件落盘、工具注册、调用循环、结果回填、可下载文件或实际预览入口、超时/持久化策略。脚本只负责本次工具请求内的确定性业务动作。

注册工具名为 `service_workbench`。执行时将工具参数作为 JSON 通过 stdin 传入：

```sh
python3 scripts/agent_bridge.py --workspace-root <宿主预先创建的用户目录>
```

依次发送的请求示例（每次启动桥接进程，单次输入一个对象）：

```json
{"action":"capabilities"}
```

```json
{"action":"init","output":"project","mode":"project"}
```

```json
{"action":"profile","file":"project/inputs/tasks.csv"}
```

```json
{"action":"build","config_path":"project/blueprint.json","output":"project/app"}
```

```json
{"action":"inspect","workspace":"project/app"}
```

```json
{"action":"package","workspace":"project/app","output":"downloads/workbench.zip"}
```

完整字段映射通过 `configure` 的 `blueprint` 对象提交，结构同 `assets/blueprint.example.json`。`refresh` 额外要求 `full_snapshot:true`，表示已确认完整快照语义，不代替用户授权。

桥接 stdout 始终为一个 JSON：`{"ok":true,"result":...}` 或 `{"ok":false,"error":...,"error_type":...}`。宿主必须检查 ok，失败不能向用户报告成功。所有路径均相对固定工作根目录；来源路径也要在其内，不接受模型任意指定系统目录。不要把用户工作目录放进 Skill 包里。这个路径限制是应用层检查，不替代宿主真正的进程/租户隔离。

`package` 是当前用户工作台的下载包，包含该用户 SQLite；它与公开分享的空 Skill 包是两类产物。长运行 serve 由宿主管理，不通过一次性工具进程启动。云沙箱生命周期到期会影响本地文件，需使用宿主的持久存储或交付下载物。

## 普通聊天使用

将 `AGENT_PROMPT.md` 粘贴到新对话，或按产品实际支持作为智能体指令/知识附件。再给出业务用途及用户文件。附件若无法读取，改为提供表头与必要片段，或换用可读取文件的执行环境。

可用的启动语：

> 按“业务工作台生成器”流程，先确认我要解决的业务问题和已有文件。如果你能运行 Python，就用所附通用脚本实际校验和生成；如果不能，请给我字段映射、配置与可执行步骤，并明确哪些还没运行。

不要要求聊天用户使用 Codex 专属 `$skill-name` 调用语法，也不要把提示词加入聊天称为平台已经安装可执行工具。

## 维护通用指令

`AGENT_PROMPT.md` 由共同源文件合并生成：

```sh
python3 scripts/export_prompt.py --output AGENT_PROMPT.md
```

维护时改 SKILL.md 或 references，再重新生成这一个文件；业务脚本、模型、模板仍共用。平台专属 UI/接口配置放在 adapters 或宿主配置，不把平台密钥和用户数据写回包内。
