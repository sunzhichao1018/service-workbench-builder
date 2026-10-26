"""Platform-neutral bridge checks using only temporary synthetic files."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/agent_bridge.py'
SKILL_ROOT=SCRIPT.parents[1]


class AgentBridgeBehavior(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='universal-bridge-')
        self.root=Path(self.temp.name)/'用户 工作区'
        self.root.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def call(self,request,root=None):
        result=subprocess.run([sys.executable,str(SCRIPT),'--workspace-root',str(root or self.root)],
                              input=json.dumps(request,ensure_ascii=False),capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stderr,'')
        self.assertEqual(len(result.stdout.strip().splitlines()),1,'stdout must contain exactly one JSON response')
        return json.loads(result.stdout)

    def okay(self,request):
        reply=self.call(request)
        self.assertTrue(reply['ok'],reply)
        return reply['result']

    def initialized(self):
        return self.okay({'action':'init','output':'课程 项目','mode':'project'})

    def built(self):
        initial=self.initialized()
        cfg=initial['blueprint']
        cfg['title']='纯合成桥接工作台'
        cfg['modules']=['dashboard','tasks','data']
        self.okay({'action':'configure','config_path':initial['config_path'],'blueprint':cfg})
        result=self.okay({'action':'build','config_path':initial['config_path'],'output':'生成 工作台','as_of':'2026-10-26'})
        return initial,result

    def test_json_protocol_and_capability_detection(self):
        caps=self.okay({'action':'capabilities'})
        self.assertTrue(caps['python_supported'])
        self.assertTrue(caps['assets_available'])
        self.assertIn('package',caps['actions'])
        malformed=subprocess.run([sys.executable,str(SCRIPT),'--workspace-root',str(self.root)],input='{',capture_output=True,text=True)
        self.assertEqual(malformed.stderr,'')
        self.assertFalse(json.loads(malformed.stdout)['ok'])

    def test_end_to_end_config_build_inspect_package(self):
        initial,result=self.built()
        self.assertEqual(result['report']['rows']['tasks'],0)
        self.assertEqual(result['workspace'],'生成 工作台')
        inspected=self.okay({'action':'inspect','workspace':result['workspace']})
        self.assertEqual(inspected['workspace']['title'],'纯合成桥接工作台')
        self.assertNotIn('datasets',inspected)
        workspace=self.root/result['workspace']
        (workspace/'backup-private.sqlite').write_text('not a database')
        (workspace/'unrequested-file.txt').write_text('not for export')
        package=self.okay({'action':'package','workspace':result['workspace'],'output':'下载/工作台.zip'})
        self.assertTrue(package['contains_user_data'])
        with zipfile.ZipFile(self.root/package['package']) as archive:
            self.assertEqual(set(archive.namelist()),{'app/index.html','app/workspace.sqlite','app/blueprint.json','app/import-report.json','app/启动说明.txt','app/runtime/workbench.py'})
            self.assertIsNone(archive.testzip())
        self.assertFalse(self.call({'action':'package','workspace':result['workspace'],'output':package['package']})['ok'])

    def test_profile_uses_only_file_structure(self):
        path=self.root/'中文 表.csv'
        path.write_text('编号,事项\nONLY-SYNTHETIC-ID,完全虚构的任务\n',encoding='utf-8')
        result=self.okay({'action':'profile','file':path.name})
        self.assertEqual(result['rows'],1)
        self.assertEqual([c['name'] for c in result['columns']],['编号','事项'])
        self.assertNotIn('ONLY-SYNTHETIC-ID',json.dumps(result))
        self.assertFalse(self.call({'action':'profile','file':path.name,'header_row':0})['ok'])

    def test_path_escape_absolute_and_symlink_rejected(self):
        outside=Path(self.temp.name)/'outside.csv'
        outside.write_text('id\nOUTSIDE\n')
        for path in ('../outside.csv',str(outside)):
            self.assertFalse(self.call({'action':'profile','file':path})['ok'])
        linked=self.root/'外部链接.csv'
        try:
            linked.symlink_to(outside)
        except (OSError,NotImplementedError):
            return
        self.assertFalse(self.call({'action':'profile','file':linked.name})['ok'])
        target=self.root/'输出链接'
        target.symlink_to(self.temp.name,target_is_directory=True)
        self.assertFalse(self.call({'action':'init','output':'输出链接/新建'})['ok'])
        self.assertFalse(self.call({'action':'init','output':'.'})['ok'])
        self.assertFalse(self.call({'action':'capabilities'},root=SKILL_ROOT)['ok'])

    def test_blueprint_source_escape_rejected_before_build(self):
        initial=self.initialized()
        cfg=copy.deepcopy(initial['blueprint'])
        cfg['sources'][0]['path']='../../outside.csv'
        configured=self.call({'action':'configure','config_path':initial['config_path'],'blueprint':cfg})
        self.assertFalse(configured['ok'])
        path=self.root/initial['config_path']
        path.write_text(json.dumps(cfg,ensure_ascii=False))
        self.assertFalse(self.call({'action':'build','config_path':initial['config_path'],'output':'拒绝创建'})['ok'])
        self.assertFalse((self.root/'拒绝创建').exists())

    def test_refresh_needs_explicit_full_snapshot(self):
        initial,result=self.built()
        request={'action':'refresh','config_path':initial['config_path'],'output':result['workspace'],'as_of':'2026-10-26'}
        self.assertFalse(self.call(request)['ok'])
        self.assertEqual(list((self.root/result['workspace']).glob('backup-*.sqlite')),[])
        request['full_snapshot']=True
        refreshed=self.okay(request)
        self.assertIn('backup',refreshed['report'])
        self.assertEqual(len(list((self.root/result['workspace']).glob('backup-*.sqlite'))),1)

    def test_unknown_action_fields_and_nonjson_overwrite_rejected(self):
        self.assertFalse(self.call({'action':'serve'})['ok'])
        self.assertFalse(self.call({'action':'capabilities','shell':'anything'})['ok'])
        protected=self.root/'已有文件.json'
        protected.write_text('this is not a JSON configuration')
        initial=self.initialized()
        self.assertFalse(self.call({'action':'configure','config_path':protected.name,'blueprint':initial['blueprint']})['ok'])
        self.assertEqual(protected.read_text(),'this is not a JSON configuration')


if __name__=='__main__':
    unittest.main(verbosity=2)
