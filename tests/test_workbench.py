"""Behavioral regression checks; all fixtures are temporary synthetic records."""
import argparse
import contextlib
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import urllib.error
import zipfile

SCRIPT = Path(os.environ.get('WORKBENCH_SCRIPT', str(Path(__file__).resolve().parents[1] / 'scripts' / 'workbench.py')))
spec = importlib.util.spec_from_file_location('workbench_under_test', SCRIPT)
wb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wb)

class WorkbenchBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='workbench-behavior-')
        self.root = Path(self.temp.name)
        self.config = {'version':1,'title':'完全合成测试工作台','mode':'service','timezone':'Asia/Shanghai',
          'labels':{'object':'对象','task':'任务','execution':'验收'},
          'modules':['dashboard','tasks','objects','executions','finance','data'],
          'completion':{'strategy':'executions','execution_coverage':'complete','done_statuses':['已完成']},'excluded_statuses':[], 'sources':[]}
        self.source('objects', [{'id':'OBJ1','name':'合成对象'}])
        self.source('tasks', [
          {'id':'A','entity_id':'OBJ1','title':'按时完成','planned_quantity':'4','unit':'次','due_date':'2026-10-25'},
          {'id':'B','title':'逾期完成','planned_quantity':'1','unit':'次','due_date':'2026-10-24'},
          {'id':'C','title':'未来执行不可计入','planned_quantity':'2','unit':'次','due_date':'2026-10-25'},
          {'id':'D','title':'缺少计划','unit':'次','due_date':'2026-10-25'},
          {'id':'E','title':'零计划','planned_quantity':'0','unit':'次','due_date':'2026-10-25'},
          {'id':'F','title':'缺少到期日','planned_quantity':'1','unit':'次'},
        ])
        self.source('executions', [
          {'id':'X1','task_id':'A','quantity':'2','accepted':True,'occurred_date':'2026-10-23'},
          {'id':'X2','task_id':'A','quantity':'2','accepted':True,'occurred_date':'2026-10-24'},
          {'id':'X3','task_id':'B','quantity':'1','accepted':True,'occurred_date':'2026-10-25'},
          {'id':'X4','task_id':'C','quantity':'2','accepted':True,'occurred_date':'2026-10-27'},
          {'id':'X5','task_id':'C','quantity':'10','accepted':False,'occurred_date':'2026-10-24'},
        ])
        self.source('transactions', [
          {'id':'M1','date':'2026-10-25','type':'income','amount':'0.1','currency':'CNY','basis':'actual'},
          {'id':'M2','date':'2026-10-25','type':'income','amount':'0.2','currency':'CNY','basis':'actual'},
          {'id':'M3','date':'2026-10-25','type':'cost','amount':'0.1','currency':'CNY','basis':'actual'},
          {'id':'M4','date':'2026-10-25','type':'income','amount':'100','currency':'USD','basis':'actual'},
          {'id':'M5','date':'2026-10-25','type':'income','amount':'999','currency':'CNY','basis':'estimate'},
          {'id':'M6','date':'2026-10-25','type':'income','amount':'888','currency':'CNY'},
          {'id':'M7','date':'2026-10-27','type':'income','amount':'777','currency':'CNY','basis':'actual'},
        ])
    def tearDown(self):
        self.temp.cleanup()
    def source(self, dataset, rows):
        path = self.root/(dataset+'.json')
        path.write_text(json.dumps(rows, ensure_ascii=False))
        old = [s for s in self.config['sources'] if s['dataset']==dataset]
        self.config['sources'] = [s for s in self.config['sources'] if s['dataset']!=dataset]
        self.config['sources'].append({'dataset':dataset,'path':path.name})
    def normalized(self):
        return wb.normalize(self.config,self.root)
    def make_workspace(self):
        cfg_path = self.root/'blueprint.json'
        cfg_path.write_text(json.dumps(self.config,ensure_ascii=False))
        output = self.root/'generated'
        args=argparse.Namespace(config=str(cfg_path),output=str(output),as_of='2026-10-26',command='build',report=None)
        with contextlib.redirect_stdout(io.StringIO()):
            wb.materialize(args)
        return output
    def test_service_status_and_rates(self):
        output=self.make_workspace()
        state=wb.state(output)
        tasks={t['id']:t for t in state['task_views']}
        self.assertEqual(tasks['A']['fulfillment_status'],'已完成')
        self.assertEqual(tasks['A']['completion_date'],'2026-10-24')
        self.assertEqual(tasks['B']['fulfillment_status'],'逾期完成')
        self.assertEqual(tasks['C']['fulfillment_status'],'逾期未完成')
        self.assertEqual(tasks['C']['accepted_quantity'],'0')
        self.assertEqual(tasks['D']['fulfillment_status'],'待确认')
        self.assertIsNone(tasks['D']['remaining_quantity'])
        self.assertEqual(tasks['E']['fulfillment_status'],'待确认')
        cards={c['label']:c for c in state['summary']['cards']}
        self.assertEqual(cards['到期达成率']['value'],'66.7')
        self.assertEqual(cards['按期完成率']['value'],'33.3')
        self.assertEqual(cards['待确认']['value'],2)
    def test_finance_decimal_currency_and_basis(self):
        state=wb.state(self.make_workspace())
        by_currency={r['currency']:r for r in state['summary']['finance']}
        self.assertEqual(by_currency['CNY']['income'],'0.3')
        self.assertEqual(by_currency['CNY']['cost'],'0.1')
        self.assertEqual(by_currency['CNY']['net'],'0.2')
        self.assertEqual(by_currency['USD']['income'],'100')
        self.assertEqual(len(state['datasets']['transactions']),7)
    def test_invalid_primary_and_foreign_keys_reject_without_workspace(self):
        self.source('objects',[{'id':'DUP','name':'一'},{'id':'DUP','name':'二'}])
        self.source('tasks',[{'id':'T','title':'孤立关联','entity_id':'MISSING'}])
        _,_,_,errors=self.normalized()
        self.assertTrue(any('重复业务 ID' in e['error'] for e in errors))
        self.assertTrue(any('entity_id' in e['error'] for e in errors))
        with self.assertRaises(wb.DataError):
            self.make_workspace()
        self.assertFalse((self.root/'generated').exists())
        self.assertTrue((self.root/'generated.validation-errors.json').is_file())
    def test_refresh_preserves_followup_and_records_count(self):
        output=self.make_workspace()
        with sqlite3.connect(output/'workspace.sqlite') as db:
            db.execute('INSERT INTO task_updates VALUES(?,?,?,?)',('A','跟进中','合成备注','2026-10-26'))
        cfg_path=output/'blueprint.json'
        args=argparse.Namespace(config=str(cfg_path),output=str(output),as_of='2026-10-26',command='refresh',report=None)
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()): wb.materialize(args)
        state=wb.state(output)
        self.assertEqual(len(state['task_views']),6)
        a=next(t for t in state['task_views'] if t['id']=='A')
        self.assertEqual(a['tracking_status'],'跟进中')
        self.assertEqual(a['note'],'合成备注')
        self.assertEqual(len(list(output.glob('backup-*.sqlite'))),2)
    def test_csv_chinese_mapping_defaults_enum_and_bom(self):
        path=self.root/'中文.csv'
        path.write_text('\ufeff编号,标题,状态\nT1,合成任务,完成\n',encoding='utf-8')
        self.config['sources']=[{'dataset':'tasks','path':path.name,
          'fields':{'id':'编号','title':'标题','status':'状态'},
          'values':{'status':{'完成':'已完成'}},'defaults':{'unit':'次','planned_quantity':'1'}}]
        rec,_,_,err=self.normalized()
        self.assertEqual(err,[])
        self.assertEqual(rec['tasks'][0]['status'],'已完成')
        self.assertEqual(rec['tasks'][0]['planned_quantity'],'1')
    def write_xlsx(self,path,multi=False,formula=False):
        # A second, unrelated merged sheet must not break explicitly selected first sheet.
        sheets='<sheet name="明细" sheetId="1" r:id="rId1"/>'
        rels='<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        if multi:
            sheets+='<sheet name="说明" sheetId="2" r:id="rId2"/>'
            rels+='<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
        def txt(pos,value): return '<c r="'+pos+'" t="inlineStr"><is><t>'+value+'</t></is></c>'
        first='<worksheet xmlns="'+wb.NS['s']+'"><sheetData><row r="1">'+txt('A1','id')+txt('B1','title')+txt('C1','due_date')+'</row><row r="2">'+txt('A2','T1')+txt('B2','合成')+'<c r="C2" s="1">'+('<f>TODAY()</f>' if formula else '<v>46321</v>')+'</c></row></sheetData></worksheet>'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="'+wb.NS['s']+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+sheets+'</sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rels+'</Relationships>')
            z.writestr('xl/styles.xml','<styleSheet xmlns="'+wb.NS['s']+'"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
            z.writestr('xl/worksheets/sheet1.xml',first)
            if multi: z.writestr('xl/worksheets/sheet2.xml','<worksheet xmlns="'+wb.NS['s']+'"><mergeCells><mergeCell ref="A1:B1"/></mergeCells></worksheet>')
    def test_xlsx_dates_sheet_selection_and_uncached_formula(self):
        path=self.root/'输入.xlsx'
        self.write_xlsx(path,multi=True)
        with self.assertRaises(wb.DataError): wb.read_table(path)
        headers,rows=wb.read_table(path,'明细')
        self.assertEqual(headers,['id','title','due_date'])
        self.assertEqual(rows[0][1]['due_date'],'2026-10-26')
        self.write_xlsx(path,formula=True)
        with self.assertRaises(wb.DataError): wb.read_table(path)
    def test_http_exports_followup_persistence_and_routes(self):
        output=self.make_workspace()
        process=subprocess.Popen([sys.executable,str(SCRIPT),'serve','--workspace',str(output),'--port','0'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=process.stdout.readline()
            match=re.search(r'http://127\.0\.0\.1:\d+',line)
            if not match:
                raise RuntimeError('Server did not start: '+line+' '+process.stderr.read())
            url=match.group()
            with urllib.request.urlopen(url+'/api/state') as response: payload=json.load(response)
            with urllib.request.urlopen(url+'/api/export?dataset=tasks') as response:
                data=response.read().decode('utf-8-sig')
                self.assertIn('fulfillment_status',data)
                self.assertIn('按时完成',data)
            body={'task_id':'A','tracking_status':'已跟进','note':'=SUM(1,2)','csrf_token':payload['csrf_token']}
            request=urllib.request.Request(url+'/api/task-update',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':url})
            with urllib.request.urlopen(request) as response: self.assertTrue(json.load(response)['ok'])
            after=wb.state(output)
            a=next(t for t in after['task_views'] if t['id']=='A')
            self.assertEqual(a['tracking_status'],'已跟进')
            self.assertEqual(a['status'],None)
            self.assertEqual(a['fulfillment_status'],'已完成')
            with urllib.request.urlopen(url+'/api/export?dataset=tasks') as response:
                exported=response.read().decode('utf-8-sig')
                self.assertIn("'=SUM",exported)
            for route,code in [('/missing',404),('/api/export?dataset=unknown',400)]:
                with self.assertRaises(urllib.error.HTTPError) as ex: urllib.request.urlopen(url+route)
                self.assertEqual(ex.exception.code,code)
            request=urllib.request.Request(url+'/api/task-update',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':'https://example.invalid'})
            with self.assertRaises(urllib.error.HTTPError) as ex: urllib.request.urlopen(request)
            self.assertEqual(ex.exception.code,403)
            with sqlite3.connect(output/'workspace.sqlite') as db:
                self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='follow_up'").fetchone()[0],1)
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_missing_execution_coverage_is_unknown(self):
        self.config['completion']['execution_coverage']='unknown'
        current=wb.state(self.make_workspace())
        self.assertTrue(all(t['fulfillment_status']=='待确认' for t in current['task_views']))
        self.assertTrue(all(t['accepted_quantity'] is None for t in current['task_views']))
        cards={c['label']:c for c in current['summary']['cards']}
        self.assertIsNone(cards['到期达成率']['value'])

    def test_ambiguous_numeric_format_rejected(self):
        self.assertEqual(wb.number('1,234.56'), wb.Decimal('1234.56'))
        for value in ('1,23', '1,2,3', '12,34.56'):
            with self.assertRaises(wb.DataError): wb.number(value)
    def test_transaction_object_task_conflict_rejected(self):
        self.source('objects',[{'id':'OBJ1','name':'合成对象1'},{'id':'OBJ2','name':'合成对象2'}])
        self.source('transactions',[{'id':'M1','task_id':'A','entity_id':'OBJ2','date':'2026-10-25','type':'income','amount':'10','currency':'CNY','basis':'actual'}])
        _,_,_,errors=self.normalized()
        self.assertTrue(errors, 'Conflicting object and task foreign keys must be rejected')

if __name__=='__main__': unittest.main(verbosity=2)
