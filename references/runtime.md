# 运行、配置与扩展

## 新工作台

用实际绝对路径替换示例，含空格的路径需引号。Python 3.10+，仅用标准库；某些 Windows Python 需要已有时区数据库或 tzdata，先检查可用环境。

```sh
python3 "<skill>/scripts/workbench.py" init --output "<project>" --mode service
python3 "<skill>/scripts/workbench.py" profile "<file.xlsx>" --sheet "任务明细"
python3 "<skill>/scripts/workbench.py" build --config "<project>/blueprint.json" --output "<project>/app"
python3 "<skill>/scripts/workbench.py" inspect --workspace "<project>/app"
python3 "<project>/app/runtime/workbench.py" serve --workspace "<project>/app" --port 8765
```

打开终端实际显示的 `http://127.0.0.1:端口`。仅监听本机，不监听局域网或公网。端口占用可换 `--port`；端口 0 可让系统分配。保持进程运行；它不会变成系统后台服务。不要直接双击 HTML，页面需要本机 API。

生成应用只含该用户的新 SQLite、导入报告、配置、HTML 和独立运行脚本；无需原作者数据库、飞书插件或第三方前端库。没有身份系统，本机浏览器可读本机用户的数据，不适用于直接部署为多人服务。

## blueprint 示例

下面示例仅展示配置形状，不包含用户事实。路径相对于 blueprint 所在目录；生成应用内的配置会保存真实绝对来源路径，便于以后刷新。这些用户路径不回写到 Skill。

```json
{
  "version": 1,
  "title": "项目交付工作台",
  "mode": "project",
  "timezone": "Asia/Shanghai",
  "labels": {"object": "项目", "task": "交付任务", "execution": "执行记录"},
  "modules": ["dashboard", "tasks", "objects", "data"],
  "completion": {"strategy": "status", "done_statuses": ["验收通过", "已完成"]},
  "excluded_statuses": ["已取消"],
  "sources": [
    {"dataset": "objects", "path": "inputs/项目.csv", "fields": {"id": "项目编号", "name": "项目名称"}},
    {"dataset": "tasks", "path": "inputs/任务.xlsx", "sheet": "交付明细", "header_row": 2,
     "fields": {"id": "任务编号", "entity_id": "项目编号", "title": "交付事项", "status": "当前状态", "owner": "负责人", "due_date": "应交付日", "completed_date": "实际完成日"}}
  ]
}
```

- `fields` 为“标准字段:原文件列名”。省略 fields 时只识别同名标准字段。来源列不存在就报错。
- `defaults` 可为缺少整列的字段设置**有业务依据的常量**，例如已确认所有明细为同一币种时设置 `{"currency":"CNY"}`。空单元格不会被 defaults 静默填充。
- `values` 可显式转换枚举，例如 `{"type":{"收入":"income","支出":"cost"},"basis":{"实绩":"actual","预估":"estimate"}}`。已配置枚举映射时遇到未列出的非空值会报错，不猜测映射。
- 同一 dataset 可以有多份文件，记录需有全局唯一 ID。多份工作表可分别配置 sources，重复 ID 仍阻断。
- modules 仅控制页面入口，**不是权限控制**。API 中仍有该用户全部导入记录，需要字段权限时应实现真正的服务端身份与裁剪。
- mode 是初始模型/页面选择。更复杂业务可扩展，不能将这四种模式说成完整行业系统。

## 刷新与备份

```sh
python3 "<skill>/scripts/workbench.py" refresh --config "<project>/app/blueprint.json" --output "<project>/app"
```

先确认所有 sources 都是当前想保留的完整数据。refresh 全部读取、校验，然后在本机新建 `backup-时间.sqlite` 备份，再在 SQLite 事务中替换事实快照；缺少的旧事实会从当前快照移除。源文件本身不会被改写。

相同文件再导入不会把数量翻倍；每次刷新新增导入批次与审计。人工跟进按 task_id 独立保存，刷新不会覆盖。删除任务的旧跟进保留在库中，页面只展示当前任务；业务 ID 不能被复用给新对象，否则旧跟进会错误关联。

新增/更新的**增量文件不能直接替换完整快照**。需要增量时，在项目中明确稳定主键、冲突来源、删除标记、覆盖范围，再构建合并文件或实现事务式 upsert；未经这些规则，不把底座称为增量同步。

备份含该用户的数据，保存在工作台目录。需要异地备份/恢复时另行复制保留，并实测恢复。恢复应先停止 serve，保留当前库，再在独立目录验证备份，不直接覆盖运行中的库。备份仅涵盖 SQLite，不含原文件和外部证据，需分别保存。

## 底座已实现

- 字段与枚举映射、主键/关联/类型校验、批次摘要、失败报告。
- 服务计划与验收数量、项目状态完成、到期/按期判定、未知值提示。
- 任务搜索与筛选、对象/执行/收支台账、按币种已发生收支和日期趋势。
- 跟进状态与备注持久化，修改前后值审计；它不是派单、执行编辑或验收操作。
- 完整快照刷新、预刷新备份、CSV 导出。导出为该数据集全量，页面任务筛选不改变导出范围。

## 按需扩展

仅在用户项目里扩展生成后的 `runtime/workbench.py`、`index.html` 或建立独立应用；不要修改已安装 Skill 来保存单个用户需求。

- 自定义维度/KPI：扩展标准化模型、服务端汇总和前端入口；一并验证口径与导出。新字段暂存 `_extra` 不等于已经支持该指标。
- 执行填报/验收/派单：增加受控 API、状态机、并发版本校验和审计，不用“跟进状态”替代真实业务动作。
- 周期服务：新增规则/版本与应履约生成，锁定历史口径，不由当前状态倒推历史义务。
- 团队协作：真实服务端身份、角色/字段/行权限、事务并发、迁移、独立备份与访问入口验证。
- 自动同步：来源授权、稳定增量键/分页/删除语义、调度、失败重试与对账；浏览器刷新和本机进程不等于后台持续同步。

已有自定义应用更新时，refresh 只更新 SQLite 事实与配置，不覆盖其 HTML/runtime。新 Skill 版本的代码升级需另行合并，不能刷新数据时顺带替换定制功能。
