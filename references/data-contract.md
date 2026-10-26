# 文件与字段契约

所有文件都是用户自己提供或填入的事实。本包的 CSV 只有表头，不含业务数据。四张表可按场景裁剪；没有收支需求就不要求收支文件。读取原始文件后通过 `blueprint.sources` 映射，不强制用户改列名。

## 数据集

| 数据集/模板 | 一行代表什么 | 必填字段 | 可选字段 |
|---|---|---|---|
| objects.csv | 一个客户、项目、门店或设备等业务对象 | id、name | owner、group |
| tasks.csv | 一项任务或一个具体周期的应履约义务 | id、title | entity_id、category、status、owner、start_date、due_date、completed_date、planned_quantity、unit |
| executions.csv | 一次独立执行及其当前明确验收结果 | id、task_id、quantity、accepted、occurred_date | evidence |
| transactions.csv | 一笔有明确日期、方向和币种的收支 | id、date、type、amount、currency | entity_id、task_id、basis |

- ID 使用稳定的文本业务 ID。Excel 的长数字和前导零应存为文本，导入无法恢复已丢失的位数。跨文件同名不代表同一对象。
- tasks.entity_id / transactions.entity_id 如有值，必须关联 objects.id。executions.task_id 必须关联 tasks.id，transactions.task_id 如有值也需关联。单表任务可以不映射 entity_id。
- 同一数据集的重复 ID 阻止导入，即使重复行内容相同也需先明确去重依据。文件中的每条独立执行只计一次。
- 数值支持整数和十进制，仅接受三位一组的规范千分位逗号，例如 1,234.56；含糊的 1,23 等格式拒绝导入；不支持百分号、货币符号、负数冲正或隐含单位。金额用 Decimal 汇总。退款、冲正、借贷余额等需求应扩展模型，不能擅自改为收入或成本。
- 日期为业务时区的 `YYYY-MM-DD`，支持 `/` 分隔和中文年月日。时间戳需先按确认的时区转成日期，不能截取字符串冒充时区转换。
- accepted 接受 是/否、true/false、1/0、通过/未通过、已验收/未验收。空值无法证明验收，底座拒绝缺失的验收事实。
- evidence 是证据的引用文本/链接，底座不会上传、下载或保存证据附件原件。证据缺失产生质量提示，不能推翻已明确提供的验收事实。
- 一条执行的 quantity 沿用其任务 unit；若来源有执行单位，转换前必须核对一致。底座不跨单位相加。
- type 只允许 income / cost；basis 为 actual / estimate / unknown。只有 basis=actual 进入已发生收支汇总，缺失 basis 会作为待确认保留在台账并提示。不要未经业务依据统一填 actual。
- status 是来源状态，不强制枚举。status 策略通过 completion.done_statuses 配置完成状态，仍需 completed_date 判定截至日完成和按期。
- 未映射列保存在记录的 `_extra` 中，不自动成为指标或出现在页面；自定义字段如需展示应在生成的应用中增加对应列。`_source` 保留文件名、工作表和原行号。

## 履约与日期语义

service 模式采用 `completion.strategy=executions`。先确认执行文件覆盖全部相关任务/周期，再设置 `completion.execution_coverage=complete`；默认 unknown，无法判定时完成量和达成率显示待确认，不能将未提供的执行记录视作零。完整覆盖也可以是用户明确确认“尚无任何执行”的空文件。

在确认完整覆盖后：每条任务需有大于零的 planned_quantity 和 unit 才能判定完成。任务宜为“某对象＋某服务＋某周期”的一行，应履约分母由用户确认的计划提供，不从工单数量倒推。只有工单而没有计划时可以生成跟进台账，不能声称已生成可靠履约率。

accepted=true 且 occurred_date 不晚于截至日、也不早于任务 start_date（如提供）的执行才累计。累计首次达到计划的执行日期为完成日期。accepted=false 的执行保留展示但不计有效完成。当前验收事实按当前文件提供，不具备历史验收事件时间，不能还原“当时已知”。

project / operations 默认 `strategy=status`：来源状态命中 done_statuses 且 completed_date 不晚于截至日才算完成；命中状态但没有完成日期为待确认。可按实际需要改用 executions。

到期分母为：due_date 不晚于 as_of、未命中 excluded_statuses 且完成条件可判定的任务。到期达成率=其中已完成数/该分母；按期完成率=其中完成日不晚于到期日的数量/同一分母。逾期完成只提高达成率。未知条件与缺到期日分别显式提示，不当作 0 或已完成。

as_of 按**所选自然日结束时**计算，due_date=as_of 的未完成任务计为逾期。运行当天的工作中快照如需“到期日次日才逾期”应在用户项目里调整边界并同步修改说明与验证。底座没有工作日、暂停时钟、宽限期、规则版本或自动周期义务生成；这些需根据实际规则扩展。

## 文件整理

- CSV/TSV：UTF-8（含 BOM）或 GB18030；首行默认表头，可用 header_row 指定。空行忽略，小计/合计行需要显式排除，不能作为业务记录。
- XLSX：指定 sheet 和 header_row；多工作表必须指明一个。仅处理所选表；合并单元格需先展开。读取公式缓存而不执行公式，缺缓存/错误单元格会阻断。隐藏行也读取，需在转换规则中决定是否排除。
- XLS 请另存为 XLSX/CSV。暂不直接读取 PDF、图片、压缩包、数据库备份；有需要时另做结构提取并与原件核对。
- JSON 为对象数组，JSONL 每行一个扁平对象；嵌套数据先展开。单文件上限 30MB、每表 50,000 条；大数据量应拆批或采用查询后端，不能无限塞进浏览器。
