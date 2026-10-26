#!/usr/bin/env python3
"""Portable local workbench: Python 3.10+ standard library, no network dependencies."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VERSION = '1.1.0'
DATASETS = ('objects', 'tasks', 'executions', 'transactions')
SCHEMA = {
    'objects': {'id':'str', 'name':'str', 'owner':'str', 'group':'str'},
    'tasks': {'id':'str', 'entity_id':'str', 'title':'str', 'category':'str', 'status':'str',
              'owner':'str', 'start_date':'date', 'due_date':'date', 'completed_date':'date',
              'planned_quantity':'number', 'unit':'str'},
    'executions': {'id':'str', 'task_id':'str', 'quantity':'number', 'accepted':'bool',
                   'occurred_date':'date', 'evidence':'str'},
    'transactions': {'id':'str', 'entity_id':'str', 'task_id':'str', 'date':'date',
                     'type':'str', 'amount':'number', 'currency':'str', 'basis':'str'},
}
REQUIRED = {'objects':('id','name'), 'tasks':('id','title'),
            'executions':('id','task_id','quantity','accepted','occurred_date'),
            'transactions':('id','date','type','amount','currency')}
MAX_BYTES = 30 * 1024 * 1024
MAX_ROWS = 50000
MAX_CELLS = 2000000
NS = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
TRACKING = ('待跟进', '跟进中', '已跟进')

class DataError(ValueError):
    pass

def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def nonempty(value):
    return value is not None and str(value).strip() != ''

def number(value):
    try:
        text_value = str(value).strip()
        if len(text_value)>128:
            raise InvalidOperation
        if ',' in text_value and not re.fullmatch(r'[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?',text_value):
            raise InvalidOperation
        plain = text_value.replace(',','')
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',plain):
            raise InvalidOperation
        n = Decimal(plain)
        if not n.is_finite() or abs(n.adjusted())>100 or abs(n.as_tuple().exponent)>100:
            raise InvalidOperation
        return n
    except (InvalidOperation, ValueError):
        raise DataError(f'不是有效数值：{value!r}')

def decimal_text(value):
    return format(value, 'f')

def parse_date(value):
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    s = str(value).strip().replace('/', '-').replace('年','-').replace('月','-').replace('日','')
    # Do not silently truncate timestamps: dates are business-local calendar days.
    if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', s):
        raise DataError(f'日期需为 YYYY-MM-DD，含时间的字段请先按业务时区转成日期：{value!r}')
    try:
        return date(*map(int, s.split('-'))).isoformat()
    except ValueError:
        raise DataError(f'无效日期：{value!r}')

def parse_bool(value):
    s = str(value).strip().casefold()
    if s in ('1','true','yes','是','通过','已验收'):
        return True
    if s in ('0','false','no','否','未通过','未验收'):
        return False
    raise DataError(f'验收值必须明确为 是/否 或 true/false：{value!r}')

def xlsx_tables(path, selected=None):
    """Read OOXML values, never execute formulas or resolve external relationships."""
    with zipfile.ZipFile(path) as archive:
        if sum(i.file_size for i in archive.infolist()) > 100 * 1024 * 1024:
            raise DataError('Excel 解压后超过 100MB，请拆分文件')
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [''.join(t.text or '' for t in si.findall('.//s:t', NS)) for si in root]
        date_styles = set()
        if 'xl/styles.xml' in archive.namelist():
            styles = ET.fromstring(archive.read('xl/styles.xml'))
            fmts = {int(e.attrib['numFmtId']): e.attrib.get('formatCode','') for e in styles.findall('s:numFmts/s:numFmt',NS)}
            for idx, xf in enumerate(styles.findall('s:cellXfs/s:xf',NS)):
                fid = int(xf.attrib.get('numFmtId','0'))
                code = re.sub(r'"[^\"]*"|\[[^\]]*\]|\\.', '', fmts.get(fid,'')).lower()
                if fid in set(range(14,23)) | set(range(27,37)) | set(range(45,48)) | set(range(50,59)) or re.search(r'[yd]', code):
                    date_styles.add(idx)
        wb = ET.fromstring(archive.read('xl/workbook.xml'))
        wp = wb.find('s:workbookPr', NS)
        epoch = datetime(1904,1,1) if wp is not None and wp.attrib.get('date1904') in ('1','true') else datetime(1899,12,30)
        rels = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        targets = {r.attrib['Id']:r.attrib['Target'] for r in rels if r.attrib.get('TargetMode') != 'External'}
        sheets = wb.findall('s:sheets/s:sheet', NS)
        if selected is None and len(sheets) != 1:
            raise DataError('Excel 包含多个工作表，请通过 --sheet 或 sources[].sheet 指定一个')
        for sheet in sheets:
            if selected is not None and sheet.attrib['name'] != selected:
                continue
            rid = sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
            target = targets.get(rid,'')
            name = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if name not in archive.namelist():
                raise DataError('Excel 工作表引用无效')
            xml = ET.fromstring(archive.read(name))
            if xml.find('s:mergeCells', NS) is not None:
                raise DataError(f'工作表 {sheet.attrib["name"]} 含合并单元格，请先展开为单行表头明细表')
            rows = []
            count = 0
            for row in xml.findall('s:sheetData/s:row', NS):
                row_number = int(row.attrib.get('r', len(rows)+1))
                if row_number > MAX_ROWS + 100:
                    raise DataError('Excel 行数超过限制，请拆分文件')
                while len(rows) < row_number:
                    rows.append([])
                values = rows[row_number-1]
                for cell in row.findall('s:c', NS):
                    count += 1
                    if count > MAX_CELLS:
                        raise DataError('Excel 单元格数量超过限制')
                    match = re.match(r'([A-Z]+)', cell.attrib.get('r',''))
                    if not match:
                        raise DataError('Excel 单元格坐标无效')
                    col = 0
                    for c in match.group(1):
                        col = col * 26 + ord(c)-64
                    if col > 1000:
                        raise DataError('列数超过 1000，请移除空白格式列')
                    while len(values) < col:
                        values.append('')
                    node = cell.find('s:v', NS)
                    value = node.text if node is not None else ''
                    kind = cell.attrib.get('t','')
                    if cell.find('s:f', NS) is not None and not nonempty(value):
                        raise DataError(f'公式缺少缓存值：{sheet.attrib["name"]}!{cell.attrib.get("r")}；请用 Excel 重算另存或转成值')
                    if kind == 'e':
                        raise DataError(f'Excel 错误单元格：{cell.attrib.get("r")}')
                    if kind == 's':
                        value = shared[int(value)]
                    elif kind == 'inlineStr':
                        value = ''.join(t.text or '' for t in cell.findall('.//s:t', NS))
                    elif kind == 'b':
                        value = 'true' if value == '1' else 'false'
                    elif nonempty(value) and int(cell.attrib.get('s','0')) in date_styles:
                        instant = epoch + timedelta(days=float(value))
                        value = instant.date().isoformat() if instant.time() == datetime.min.time() else instant.isoformat()
                    values[col-1] = value or ''
            yield sheet.attrib['name'], rows

def headers_and_rows(grid, header_row=1):
    if header_row < 1 or header_row > len(grid):
        raise DataError('表头行不在文件范围内')
    headers = [str(v).strip() if v is not None else '' for v in grid[header_row-1]]
    # Trim trailing empty cells; interior empty headers are ambiguous.
    while headers and not headers[-1]:
        headers.pop()
    if not headers or any(not h for h in headers):
        raise DataError('表头包含空列名，请先整理')
    if len(set(headers)) != len(headers):
        raise DataError('表头存在重复列名')
    rows = []
    for idx, values in enumerate(grid[header_row:], header_row+1):
        if not any(nonempty(v) for v in values):
            continue
        if len(values) > len(headers) and any(nonempty(v) for v in values[len(headers):]):
            raise DataError(f'第 {idx} 行出现没有表头的数据列')
        rows.append((idx, dict(zip(headers, list(values) + [''] * (len(headers)-len(values))))))
    if len(rows) > MAX_ROWS:
        raise DataError(f'超过 {MAX_ROWS} 行，请分批或使用数据库方案')
    return headers, rows

def read_table(path, sheet=None, header_row=1):
    path = Path(path)
    if not path.is_file():
        raise DataError(f'找不到文件：{path}')
    if path.stat().st_size > MAX_BYTES:
        raise DataError('单文件超过 30MB，请拆分或使用数据库方案')
    suffix = path.suffix.lower()
    if suffix == '.xlsx':
        tables = list(xlsx_tables(path, sheet))
        if sheet is None and len(tables) != 1:
            raise DataError('Excel 包含多个工作表，请通过 --sheet 或 sources[].sheet 指定一个')
        matches = [grid for name, grid in tables if sheet is None or name == sheet]
        if not matches:
            raise DataError(f'找不到工作表：{sheet}')
        return headers_and_rows(matches[0], header_row)
    if suffix in ('.csv','.tsv'):
        raw = path.read_bytes()
        try:
            content = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            content = raw.decode('gb18030')
        return headers_and_rows(list(csv.reader(io.StringIO(content), delimiter='\t' if suffix == '.tsv' else ',')), header_row)
    if suffix in ('.json','.jsonl'):
        raw = path.read_text(encoding='utf-8-sig')
        records = [json.loads(line) for line in raw.splitlines() if line.strip()] if suffix == '.jsonl' else json.loads(raw)
        if not isinstance(records, list) or any(not isinstance(v,dict) for v in records):
            raise DataError('JSON 必须为对象数组，JSONL 必须每行一个对象')
        if len(records) > MAX_ROWS:
            raise DataError('JSON 超过行数限制')
        headers = list(dict.fromkeys(k for rec in records for k in rec))
        if any(not isinstance(k,str) or not k.strip() for k in headers):
            raise DataError('JSON 字段名必须为非空字符串')
        if any(isinstance(v,(dict,list)) for rec in records for v in rec.values()):
            raise DataError('暂不支持嵌套 JSON，请先展开字段')
        return headers, [(i+1,{h:rec.get(h,'') for h in headers}) for i,rec in enumerate(records)]
    raise DataError('支持 .csv/.tsv/.xlsx/.json/.jsonl；.xls 请另存为 .xlsx 或 CSV')

def profile(args):
    headers, rows = read_table(args.file, args.sheet, args.header_row)
    columns = []
    for h in headers:
        vals = [r[h] for _,r in rows if nonempty(r[h])]
        types = Counter()
        for v in vals:
            for name, parser in [('number',number),('date',parse_date),('boolean',parse_bool)]:
                try:
                    parser(v)
                    types[name] += 1
                    break
                except (DataError, TypeError):
                    pass
            else:
                types['text'] += 1
        columns.append({'name':h,'missing':len(rows)-len(vals),'distinct':len(set(map(str,vals))),'types':dict(types)})
    result = {'file_name':Path(args.file).name,'rows':len(rows),'columns':columns,'note':'仅统计结构，不输出原始行；类型为候选，业务主键和指标口径需确认。'}
    if args.output:
        dump(args.output, result)
    else:
        print(json.dumps(result,ensure_ascii=False,indent=2))

def load_blueprint(path):
    config_path = Path(path).resolve()
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    if cfg.get('version') != 1:
        raise DataError('blueprint.version 必须为 1')
    if cfg.get('mode') not in ('service','project','operations','sales'):
        raise DataError('mode 必须为 service/project/operations/sales')
    if not str(cfg.get('title','')).strip():
        raise DataError('必须提供工作台 title')
    try:
        ZoneInfo(cfg.get('timezone','Asia/Shanghai'))
    except Exception:
        raise DataError('无效时区；Windows 如缺少时区数据库，请安装 tzdata 或使用已有 Python 运行环境')
    cfg.setdefault('timezone','Asia/Shanghai')
    cfg.setdefault('labels', {'object':'业务对象','task':'任务','execution':'执行记录'})
    cfg.setdefault('modules', ['dashboard','tasks','objects','executions','finance','data'])
    if not isinstance(cfg['modules'],list) or set(cfg['modules']) - {'dashboard','tasks','objects','executions','finance','data'}:
        raise DataError('modules 含未知模块')
    cfg.setdefault('completion', {'strategy':'executions' if cfg['mode']=='service' else 'status','done_statuses':['已完成']})
    c = cfg['completion']
    if c.get('strategy') not in ('executions','status') or not isinstance(c.get('done_statuses',[]),list):
        raise DataError('completion.strategy 需为 executions 或 status，done_statuses 需为列表')
    c.setdefault('execution_coverage','unknown')
    if c['execution_coverage'] not in ('complete','unknown'):
        raise DataError('completion.execution_coverage 需为 complete 或 unknown')
    if cfg['mode']=='service' and c['strategy']!='executions':
        raise DataError('服务履约模式需采用验收执行策略；仅状态表请使用 project/operations 并标明局限')
    cfg.setdefault('excluded_statuses', [])
    if not isinstance(cfg['excluded_statuses'], list):
        raise DataError('excluded_statuses 需为已确认不纳入考核的来源状态列表')
    if not isinstance(cfg.get('sources'),list) or not cfg['sources']:
        raise DataError('sources 至少配置一个文件')
    return cfg, config_path.parent

def normalize(cfg, base):
    result = {k:[] for k in DATASETS}
    batches = []
    errors, warnings = [], []
    seen = {k:set() for k in DATASETS}
    for source in cfg['sources']:
        dataset = source.get('dataset')
        if dataset not in DATASETS:
            raise DataError(f'未知 dataset：{dataset}')
        path = (base / source['path']).resolve()
        headers, rows = read_table(path, source.get('sheet'), source.get('header_row',1))
        mapping = source.get('fields', {f:f for f in SCHEMA[dataset] if f in headers})
        if not isinstance(mapping,dict) or set(mapping)-set(SCHEMA[dataset]):
            raise DataError(f'{dataset} 字段映射包含不支持的标准字段')
        defaults = source.get('defaults',{})
        if not isinstance(defaults,dict) or set(defaults)-set(SCHEMA[dataset]):
            raise DataError(f'{dataset} defaults 包含未知字段')
        for target, origin in mapping.items():
            if not isinstance(origin,str) or origin not in headers:
                raise DataError(f'{dataset}.{target} 映射的来源列不存在：{origin}')
        for required in REQUIRED[dataset]:
            if required not in mapping and required not in defaults:
                raise DataError(f'{dataset} 缺少必需映射：{required}')
        for line, raw in rows:
            row = {}
            try:
                for field, kind in SCHEMA[dataset].items():
                    val = raw.get(mapping.get(field,''), defaults.get(field,''))
                    translations = source.get('values',{}).get(field,{})
                    if translations and nonempty(val):
                        if str(val).strip() not in translations:
                            raise DataError(f'{field} 存在未映射取值：{val!r}')
                        val = translations[str(val).strip()]
                    if not nonempty(val):
                        row[field] = None
                    elif kind == 'number':
                        n = number(val)
                        if n < 0:
                            raise DataError(f'{field} 不得为负数；冲正需单独建模，不能静默变成收入/成本')
                        row[field] = decimal_text(n)
                    elif kind == 'date':
                        row[field] = parse_date(val)
                    elif kind == 'bool':
                        row[field] = parse_bool(val)
                    else:
                        row[field] = str(val).strip()
                missing = [f for f in REQUIRED[dataset] if row[f] is None]
                if missing:
                    raise DataError('缺少必填值：'+','.join(missing))
                if row['id'] in seen[dataset]:
                    raise DataError(f'重复业务 ID：{row["id"]}')
                if dataset == 'transactions' and row['type'] not in ('income','cost'):
                    raise DataError('type 必须通过预处理映射为 income 或 cost')
                if dataset == 'transactions' and row.get('basis') not in (None,'actual','estimate','unknown'):
                    raise DataError('basis 必须为 actual/estimate/unknown')
                if dataset == 'tasks' and row['start_date'] and row['due_date'] and row['start_date'] > row['due_date']:
                    raise DataError('开始日期晚于到期日期')
                seen[dataset].add(row['id'])
                row['_source'] = {'file':path.name,'sheet':source.get('sheet'),'row':line}
                row['_extra'] = {h:v for h,v in raw.items() if h not in mapping.values()}
                result[dataset].append(row)
            except DataError as exc:
                errors.append({'dataset':dataset,'file':path.name,'row':line,'error':str(exc)})
        batches.append({'dataset':dataset,'file_name':path.name,'row_count':len(rows),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    objects, tasks = seen['objects'], seen['tasks']
    task_lookup = {row['id']:row for row in result['tasks']}
    for dataset, rows in result.items():
        for row in rows:
            if row.get('entity_id') and row['entity_id'] not in objects:
                errors.append({'dataset':dataset,'id':row['id'],'error':'entity_id 没有对应业务对象；需补对象表或移除此关联映射'})
            if row.get('task_id') and row['task_id'] not in tasks:
                errors.append({'dataset':dataset,'id':row['id'],'error':'task_id 没有对应任务'})
            if dataset == 'transactions' and row.get('entity_id') and row.get('task_id') in task_lookup:
                task_entity = task_lookup[row['task_id']].get('entity_id')
                if task_entity and task_entity != row['entity_id']:
                    errors.append({'dataset':dataset,'id':row['id'],'error':'交易 entity_id 与所关联任务的 entity_id 不一致'})
            if dataset == 'executions' and row['accepted'] and not row.get('evidence'):
                warnings.append(f'执行 {row["id"]} 已验收但没有证据引用；不会擅自撤销已确认验收。')
    if not result['tasks']:
        warnings.append('尚无任务数据，任务指标为空。')
    if cfg['completion']['strategy']=='executions':
        if cfg['completion'].get('execution_coverage')!='complete':
            warnings.append('执行数据是否完整尚未确认，履约条件标为待确认；不能把未提供的验收当作零。')
        incomplete = sum(r['planned_quantity'] is None or not r['unit'] or not r['due_date'] for r in result['tasks'])
        if incomplete:
            warnings.append(f'{incomplete} 项任务缺少计划数量、单位或到期日期，相关履约判定显示待确认或不纳入到期分母。')
    unconfirmed_finance = sum(r.get('basis') != 'actual' for r in result['transactions'])
    if unconfirmed_finance:
        warnings.append(f'{unconfirmed_finance} 条收支不是已确认实绩（actual），保留在台账但不计入已发生收支。')
    if not result['transactions']:
        warnings.append('没有收支数据，不生成虚构金额。')
    return result, batches, list(dict.fromkeys(warnings)), errors

DDL = '''
CREATE TABLE IF NOT EXISTS records(dataset TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(dataset,id));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_batches(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,dataset TEXT NOT NULL,file_name TEXT,row_count INTEGER NOT NULL,sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_updates(task_id TEXT PRIMARY KEY,tracking_status TEXT NOT NULL,note TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,action TEXT NOT NULL,task_id TEXT,before_json TEXT,after_json TEXT);
'''

def now():
    return datetime.now(timezone.utc).isoformat()

def write_snapshot(db, cfg, records, batches, warnings, as_of):
    db.executescript(DDL)
    stamp = now()
    with db:
        db.execute('DELETE FROM records')
        for dataset, rows in records.items():
            db.executemany('INSERT INTO records VALUES(?,?,?)',[(dataset,r['id'],json.dumps(r,ensure_ascii=False)) for r in rows])
        for key, value in [('workspace',cfg),('warnings',warnings),('as_of',as_of)]:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
        for batch in batches:
            db.execute('INSERT INTO import_batches(created_at,dataset,file_name,row_count,sha256) VALUES(?,?,?,?,?)',
                       (stamp,batch['dataset'],batch['file_name'],batch['row_count'],batch['sha256']))
        db.execute('INSERT INTO audit(created_at,action,after_json) VALUES(?,?,?)',
                   (stamp,'snapshot_import',json.dumps({'as_of':as_of,'rows':{k:len(v) for k,v in records.items()}},ensure_ascii=False)))

def materialize(args):
    cfg, base = load_blueprint(args.config)
    for source in cfg['sources']:
        source['path'] = str((base / source['path']).resolve())
    records, batches, warnings, errors = normalize(cfg, base)
    output = Path(args.output).resolve()
    if errors:
        # Rejected imports do not create or alter a workspace database.
        report = Path(args.report).resolve() if args.report else Path(str(output)+'.validation-errors.json')
        dump(report, {'ok':False,'errors':errors,'warnings':warnings})
        raise DataError(f'发现 {len(errors)} 项错误，未导入。报告：{report}')
    as_of = parse_date(args.as_of) if args.as_of else datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat()
    if args.command == 'refresh':
        if not (output/'workspace.sqlite').is_file():
            raise DataError('refresh 的 --output 必须为已生成工作台目录')
        # Full snapshot: all sources in blueprint are authoritative. Keep manual follow-ups separate.
        backup = output/('backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.sqlite')
        with sqlite3.connect(output/'workspace.sqlite') as db, sqlite3.connect(backup) as copy:
            db.backup(copy)
        with sqlite3.connect(output/'workspace.sqlite') as db:
            write_snapshot(db,cfg,records,batches,warnings,as_of)
        dump(output/'blueprint.json',cfg)
        dump(output/'import-report.json',{'ok':True,'rows':{k:len(v) for k,v in records.items()},'warnings':warnings,'as_of':as_of,'backup':backup.name})
    else:
        if output.exists():
            raise DataError('输出目录已存在；请使用新目录，更新已有工作台请明确使用 refresh')
        output.parent.mkdir(parents=True,exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.workbench-',dir=output.parent))
        try:
            with sqlite3.connect(stage/'workspace.sqlite') as db:
                write_snapshot(db,cfg,records,batches,warnings,as_of)
            dump(stage/'blueprint.json',cfg)
            dump(stage/'import-report.json',{'ok':True,'rows':{k:len(v) for k,v in records.items()},'warnings':warnings,'as_of':as_of})
            (stage/'runtime').mkdir()
            shutil.copy2(Path(__file__),stage/'runtime/workbench.py')
            shutil.copy2(ROOT/'assets/workbench.html',stage/'index.html')
            (stage/'启动说明.txt').write_text('启动：python3 runtime/workbench.py serve --workspace .\n在终端进入本目录后运行。仅监听本机；保持终端开启。\n数据保存在 workspace.sqlite。请同时备份数据库和导入文件。\n刷新时应使用已安装 Skill 中的 workbench.py 和原始来源路径。\n',encoding='utf-8')
            stage.rename(output)
        except Exception:
            shutil.rmtree(stage,ignore_errors=True)
            raise
    print(json.dumps({'ok':True,'workspace':str(output),'as_of':as_of,'rows':{k:len(v) for k,v in records.items()},'warnings':warnings},ensure_ascii=False,indent=2))

def state(workspace):
    with sqlite3.connect(workspace/'workspace.sqlite') as db:
        meta = {k:json.loads(v) for k,v in db.execute('SELECT key,value FROM meta')}
        datasets = {k:[] for k in DATASETS}
        for dataset,payload in db.execute('SELECT dataset,payload FROM records ORDER BY dataset,id'):
            datasets[dataset].append(json.loads(payload))
        updates = {r[0]:{'tracking_status':r[1],'note':r[2]} for r in db.execute('SELECT task_id,tracking_status,note FROM task_updates')}
        db.row_factory = sqlite3.Row
        batches = [dict(r) for r in db.execute('SELECT * FROM import_batches ORDER BY id DESC LIMIT 100')]
    cfg, as_of = meta['workspace'], meta['as_of']
    objects = {r['id']:r for r in datasets['objects']}
    executions = defaultdict(list)
    warnings = list(meta['warnings'])
    for row in datasets['executions']:
        executions[row['task_id']].append(row)
    views = []
    due_count = achieved = on_time = overdue = excluded = 0
    unknown = 0
    for task in datasets['tasks']:
        row = dict(task)
        row.update(updates.get(task['id'], {'tracking_status':'待跟进','note':''}))
        row['object_name'] = objects.get(task.get('entity_id'),{}).get('name','未关联对象')
        start = task.get('start_date')
        valid = [e for e in executions[task['id']] if e['accepted'] and e['occurred_date']<=as_of and (not start or e['occurred_date']>=start)]
        qty = sum((number(e['quantity']) for e in valid),Decimal(0))
        planned = number(task['planned_quantity']) if task['planned_quantity'] is not None else None
        due = task['due_date']
        coverage = cfg['completion'].get('execution_coverage')=='complete'
        row['accepted_quantity'] = decimal_text(qty) if coverage else None
        row['remaining_quantity'] = decimal_text(max(Decimal(0),planned-qty)) if planned is not None and coverage else None
        is_excluded = task.get('status') in cfg['excluded_statuses']
        done_by = None
        known = True
        if cfg['completion']['strategy']=='executions':
            known = planned is not None and planned > 0 and bool(task.get('unit')) and coverage
            if known:
                running = Decimal(0)
                for execution in sorted(valid,key=lambda e:(e['occurred_date'],e['id'])):
                    running += number(execution['quantity'])
                    if running >= planned:
                        done_by = execution['occurred_date']
                        break
        else:
            # A done status without a dated fact cannot establish completion as-of or on-time.
            if task.get('status') in cfg['completion'].get('done_statuses',[]):
                if task.get('completed_date'):
                    if task['completed_date'] <= as_of:
                        done_by = task['completed_date']
                else:
                    known = False
            row['accepted_quantity'] = None
            row['remaining_quantity'] = None
        if is_excluded:
            row['fulfillment_status'] = '不纳入考核'
            excluded += 1
        elif not known:
            row['fulfillment_status'] = '待确认'
            unknown += 1
        elif done_by:
            row['fulfillment_status'] = '逾期完成' if due and done_by > due else '已完成'
        elif due and due <= as_of:
            row['fulfillment_status'] = '逾期未完成'
            overdue += 1
        elif start and start > as_of:
            row['fulfillment_status'] = '未开始'
        else:
            row['fulfillment_status'] = '进行中'
        row['completion_date'] = done_by
        # Unknown obligations are excluded from the calculable denominator and explicitly counted.
        if due and due<=as_of and not is_excluded and known:
            due_count += 1
            if done_by:
                achieved += 1
                if done_by<=due:
                    on_time += 1
        views.append(row)
    if unknown:
        warnings.append(f'{unknown} 项完成条件或完成日期不明，未进入可判定到期率分母，请补齐后刷新。')
    missing_due = sum(not t['due_date'] and t['status'] not in cfg['excluded_statuses'] for t in datasets['tasks'])
    if missing_due:
        warnings.append(f'{missing_due} 项缺少到期日期，未进入到期率分母。')
    if datetime.now(ZoneInfo(cfg['timezone'])).date().isoformat() != as_of:
        warnings.append(f'当前为截至 {as_of} 的快照；逾期与指标不会随浏览器时间自动重算，请通过 refresh 更新。')
    def rate(n):
        return decimal_text((Decimal(n)*100/Decimal(due_count)).quantize(Decimal('0.1'))) if due_count else None
    cards = [
        {'label':cfg['labels'].get('object','业务对象')+'数','value':len(objects) if objects else None,'unit':'个','note':'以稳定业务 ID 去重；无数据时显示暂无'},
        {'label':cfg['labels'].get('task','任务')+'总数','value':len(views) if views else None,'unit':'项','note':f'不纳入考核 {excluded} 项；无任务数据时显示暂无'},
        {'label':'逾期未完成','value':overdue if views else None,'unit':'项','note':f'截至 {as_of}，按已确认条件判定'},
        {'label':'到期达成率','value':rate(achieved),'unit':'%','note':f'{achieved}/{due_count} 项可判定到期任务；待确认 {unknown} 项'},
        {'label':'按期完成率','value':rate(on_time),'unit':'%','note':f'{on_time}/{due_count}；无到期分母时显示暂无'},
        {'label':'待确认','value':unknown if views else None,'unit':'项','note':'完成依据不足，不能当作零或完成'},
    ]
    currencies = defaultdict(lambda:{'income':Decimal(0),'cost':Decimal(0)})
    trend = defaultdict(lambda:{'income':Decimal(0),'cost':Decimal(0)})
    for row in datasets['transactions']:
        if row['date']<=as_of and row.get('basis')=='actual':
            currencies[row['currency']][row['type']] += number(row['amount'])
            trend[(row['date'],row['currency'])][row['type']] += number(row['amount'])
    finance = [{'currency':c,**{k:decimal_text(v) for k,v in vals.items()},'net':decimal_text(vals['income']-vals['cost'])} for c,vals in sorted(currencies.items())]
    if cfg['mode']=='sales':
        cards = cards[:1]
    if 'finance' in cfg['modules']:
        for entry in finance:
            for field, label in [('income','已发生收入'),('cost','已发生成本'),('net','收支净额')]:
                cards.append({'label':label,'value':entry[field],'unit':entry['currency'],'note':'仅统计已确认实际发生的记录；各币种分别计算'})
    groups = Counter(r['fulfillment_status'] for r in views)
    public = {k:cfg[k] for k in ('title','mode','timezone','labels','modules')}
    public['as_of'] = as_of
    public['completion_strategy'] = cfg['completion']['strategy']
    return {'workspace':public,'datasets':datasets,'task_views':views,
            'summary':{'cards':cards,'groups':[{'name':k,'count':v} for k,v in groups.items()],
                       'finance':finance,'trend':[{'date':d,'currency':c,**{k:decimal_text(v) for k,v in vals.items()}} for (d,c),vals in sorted(trend.items())]},
            'quality':{'batches':batches,'warnings':list(dict.fromkeys(warnings))}}

def export_csv(current, dataset):
    rows = current['task_views'] if dataset=='tasks' else current['datasets'][dataset]
    fields = list(SCHEMA[dataset])
    if dataset=='tasks':
        fields += ['object_name','tracking_status','note','accepted_quantity','remaining_quantity','fulfillment_status']
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(fields)
    for row in rows:
        cells = []
        for field in fields:
            value = row.get(field)
            text = '' if value is None else str(value)
            if text.lstrip().startswith(('=','+','-','@','\t','\r')):
                text = "'" + text
            cells.append(text)
        writer.writerow(cells)
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def serve(args):
    workspace = Path(args.workspace).resolve()
    if not (workspace/'workspace.sqlite').is_file() or not (workspace/'index.html').is_file():
        raise DataError('该目录不是已生成的工作台')
    csrf_token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def allowed(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            return self.headers.get('Host') == expected
        def respond(self,code,body,mime='application/json; charset=utf-8',filename=None):
            self.send_response(code)
            self.send_header('Content-Type',mime)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            if not isinstance(body,bytes):
                body = json.dumps(body,ensure_ascii=False).encode('utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if not self.allowed():
                return self.respond(403,{'error':'请使用启动时显示的 127.0.0.1 地址'})
            parsed = urlparse(self.path)
            if parsed.path in ('/','/index.html'):
                return self.respond(200,(workspace/'index.html').read_bytes(),'text/html; charset=utf-8')
            if parsed.path == '/api/state':
                payload = state(workspace)
                payload['csrf_token'] = csrf_token
                return self.respond(200,payload)
            if parsed.path == '/api/export':
                dataset = parse_qs(parsed.query).get('dataset',[''])[0]
                if dataset not in DATASETS:
                    return self.respond(400,{'error':'未知数据集'})
                return self.respond(200,export_csv(state(workspace),dataset),'text/csv; charset=utf-8',dataset+'.csv')
            self.respond(404,{'error':'找不到资源'})
        def do_POST(self):
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if not self.allowed() or self.headers.get('Origin') != expected:
                return self.respond(403,{'error':'不允许跨来源操作'})
            if self.path!='/api/task-update':
                return self.respond(404,{'error':'找不到资源'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if length<1 or length>32000:
                    raise DataError('请求大小无效')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload,dict) or not secrets.compare_digest(str(payload.get('csrf_token','')),csrf_token):
                    return self.respond(403,{'error':'操作校验失败，请刷新页面'})
                task_id, tracking_status, note = payload.get('task_id'),payload.get('tracking_status'),payload.get('note','')
                if tracking_status not in TRACKING or not isinstance(note,str) or len(note)>5000:
                    raise DataError('跟进状态或备注无效，备注最多 5000 字')
                with sqlite3.connect(workspace/'workspace.sqlite') as db:
                    if not isinstance(task_id,str) or not db.execute("SELECT 1 FROM records WHERE dataset='tasks' AND id=?",(task_id,)).fetchone():
                        return self.respond(404,{'error':'任务不存在'})
                    before = db.execute('SELECT tracking_status,note,updated_at FROM task_updates WHERE task_id=?',(task_id,)).fetchone()
                    stamp = now()
                    db.execute('INSERT OR REPLACE INTO task_updates VALUES(?,?,?,?)',(task_id,tracking_status,note,stamp))
                    db.execute('INSERT INTO audit(created_at,action,task_id,before_json,after_json) VALUES(?,?,?,?,?)',
                               (stamp,'follow_up',task_id,json.dumps(before,ensure_ascii=False),json.dumps({'tracking_status':tracking_status,'note':note},ensure_ascii=False)))
                self.respond(200,{'ok':True})
            except (DataError,ValueError,TypeError) as exc:
                self.respond(400,{'error':str(exc)})
    server = HTTPServer(('127.0.0.1',args.port),Handler)
    print(f'工作台已启动：http://127.0.0.1:{server.server_port}\n本地单人模式；Ctrl+C 停止。',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

def init(args):
    output = Path(args.output).resolve()
    if output.exists():
        raise DataError('初始化目录已存在，请选择新目录')
    output.mkdir(parents=True)
    shutil.copytree(ROOT/'assets/input-templates',output/'inputs')
    cfg = json.loads((ROOT/'assets/blueprint.example.json').read_text(encoding='utf-8'))
    cfg['mode'] = args.mode
    cfg['completion']['strategy'] = 'executions' if args.mode=='service' else 'status'
    titles = {'service':'服务履约工作台','project':'项目交付工作台','operations':'运营工作台','sales':'经营分析工作台'}
    cfg['title'] = titles[args.mode]
    if args.mode!='service':
        cfg['labels']['task'] = '交付任务' if args.mode=='project' else '运营任务'
    if args.mode=='sales':
        cfg['modules'] = ['dashboard','objects','finance','data']
    dump(output/'blueprint.json',cfg)
    print(f'已创建空白模板与配置：{output}\n下一步确认用途、字段映射和口径，再导入用户文件。')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version=VERSION)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('profile',help='仅分析文件结构，不输出原始数据行')
    p.add_argument('file'); p.add_argument('--sheet'); p.add_argument('--header-row',type=int,default=1); p.add_argument('--output')
    p.set_defaults(func=profile)
    p = sub.add_parser('init',help='生成空白输入模板和配置')
    p.add_argument('--output',required=True); p.add_argument('--mode',choices=['service','project','operations','sales'],default='service')
    p.set_defaults(func=init)
    for command in ('build','refresh'):
        p = sub.add_parser(command,help='新建工作台' if command=='build' else '备份后按全部配置来源刷新快照')
        p.add_argument('--config',required=True); p.add_argument('--output',required=True); p.add_argument('--as-of'); p.add_argument('--report')
        p.set_defaults(func=materialize)
    p = sub.add_parser('serve',help='启动本地单人工作台')
    p.add_argument('--workspace',required=True); p.add_argument('--port',type=int,default=8765); p.set_defaults(func=serve)
    p = sub.add_parser('inspect',help='查看生成结果的汇总与数据质量')
    p.add_argument('--workspace',required=True)
    p.set_defaults(func=lambda a: print(json.dumps({k:v for k,v in state(Path(a.workspace).resolve()).items() if k in ('workspace','summary','quality')},ensure_ascii=False,indent=2)))
    args = parser.parse_args()
    try:
        args.func(args)
    except (DataError, OSError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, UnicodeError) as exc:
        print(f'错误：{exc}',file=sys.stderr)
        sys.exit(2)

if __name__ == '__main__':
    main()
