"""Check platform-neutral distribution using temporary host directories."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prompt_export',ROOT/'scripts/export_prompt.py')
exporter=importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class PortabilityTests(unittest.TestCase):
    def test_prompt_is_reproducible_and_self_contained(self):
        result=exporter.render()
        self.assertEqual((ROOT/'AGENT_PROMPT.md').read_text(encoding='utf-8'),result)
        for source in exporter.SOURCES:
            self.assertIn('<!-- source: '+source+' -->',result)
        self.assertNotRegex(result,r'\]\((?!https?://)[^)]+\.md\)')
        self.assertIn('工具调用参数本身不等于脚本已执行',result)

    def test_install_and_execute_in_an_unrelated_host_root(self):
        with tempfile.TemporaryDirectory(prefix='generic-agent-') as temp:
            target=Path(temp)/'其他 Agent 技能目录'
            command=[sys.executable,str(ROOT/'scripts/install.py'),'--skills-dir',str(target)]
            result=subprocess.run(command,capture_output=True,text=True,check=True)
            self.assertTrue(json.loads(result.stdout)['ok'])
            installed=target/'service-workbench-builder'
            self.assertEqual((installed/'scripts/workbench.py').read_bytes(),(ROOT/'scripts/workbench.py').read_bytes())
            version=subprocess.run([sys.executable,str(installed/'scripts/workbench.py'),'--version'],capture_output=True,text=True,check=True)
            self.assertEqual(version.stdout.strip(),'1.1.0')
            conflict=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(conflict.returncode,2)
            self.assertFalse(json.loads(conflict.stdout)['ok'])

    def test_install_cannot_recurse_into_its_own_package(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/install.py'),'--skills-dir',str(ROOT/'nested'),'--dry-run'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertFalse(json.loads(result.stdout)['ok'])

if __name__=='__main__':
    unittest.main()
