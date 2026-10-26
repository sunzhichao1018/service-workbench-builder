#!/usr/bin/env python3
"""Model-independent JSON tool bridge. Read one JSON object from stdin, write one JSON reply."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import zipfile
from zoneinfo import ZoneInfo

import workbench as wb

SKILL_ROOT = Path(__file__).resolve().parents[1]
ACTIONS = ('capabilities','init','profile','configure','build','refresh','inspect','package')
FIELDS = {'action','output','mode','file','sheet','header_row','config_path','blueprint','as_of','full_snapshot','workspace'}

class BridgeError(ValueError):
    pass


def inside(path, parent):
    return path == parent or parent in path.parents


class Bridge:
    def __init__(self, workspace_root):
        self.root = Path(workspace_root).resolve()
        if not self.root.is_dir():
            raise BridgeError('workspace-root 必须是宿主预先创建的用户工作目录')
        if inside(self.root, SKILL_ROOT):
            raise BridgeError('用户工作目录不能位于 Skill 包内')

    def path(self, value, *, allow_root=False, suffix=None):
        if not isinstance(value,str) or not value.strip():
            raise BridgeError('路径必须为非空字符串')
        candidate = (self.root / value).resolve()
        if not inside(candidate,self.root) or (candidate == self.root and not allow_root):
            raise BridgeError('路径超出用户工作目录，或指向工作目录本身')
        if inside(candidate,SKILL_ROOT):
            raise BridgeError('不得将用户数据读写到 Skill 包内')
        if suffix and candidate.suffix.lower() != suffix:
            raise BridgeError('输出扩展名必须为 '+suffix)
        return candidate

    def relative(self, path):
        return str(Path(path).relative_to(self.root))

    def sources(self, cfg, base):
        for source in cfg.get('sources',[]):
            if not isinstance(source,dict) or not isinstance(source.get('path'),str):
                raise BridgeError('每个 sources 条目必须提供文件 path')
            candidate = (base/source['path']).resolve()
            self.path(str(candidate))

    def config(self, value):
        path = self.path(value,suffix='.json')
        cfg, base = wb.load_blueprint(path)
        self.sources(cfg,base)
        return path,cfg

    def workspace(self, value):
        path = self.path(value,allow_root=True)
        if not path.is_dir():
            raise BridgeError('工作台目录不存在')
        for name in ('workspace.sqlite','index.html'):
            member=self.path(str(path/name))
            if not member.is_file():
                raise BridgeError('工作台缺少 '+name)
        return path

    @staticmethod
    def capture(func, args):
        buffer=io.StringIO()
        with contextlib.redirect_stdout(buffer):
            func(args)
        return buffer.getvalue()

    def run(self, request):
        if not isinstance(request,dict) or set(request)-FIELDS:
            raise BridgeError('请求必须为对象，且只能包含工具 schema 中的字段')
        action=request.get('action')
        if action not in ACTIONS:
            raise BridgeError('未知 action；支持 '+', '.join(ACTIONS))
        if action=='capabilities':
            try:
                ZoneInfo('Asia/Shanghai')
                timezone_available=True
            except Exception:
                timezone_available=False
            return {'bridge_version':'1.1.0','engine_version':wb.VERSION,
                    'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,10),
                    'sqlite':sqlite3.sqlite_version,'timezone_available':timezone_available,
                    'assets_available':all((SKILL_ROOT/p).is_file() for p in ('assets/workbench.html','assets/blueprint.example.json')),
                    'actions':list(ACTIONS), 'scope':'本宿主工作目录；不检测模型、账号或聊天网页能力',
                    'serve':'长运行服务由宿主管理；本工具不启动后台进程'}
        if action=='init':
            output=self.path(request.get('output'))
            mode=request.get('mode','service')
            if mode not in ('service','project','operations','sales'):
                raise BridgeError('mode 无效')
            self.capture(wb.init,argparse.Namespace(output=str(output),mode=mode))
            return {'project':self.relative(output),'config_path':self.relative(output/'blueprint.json'),
                    'blueprint':json.loads((output/'blueprint.json').read_text(encoding='utf-8')),
                    'templates':[self.relative(p) for p in sorted((output/'inputs').glob('*.csv'))]}
        if action=='profile':
            path=self.path(request.get('file'))
            header=request.get('header_row',1)
            if not isinstance(header,int) or isinstance(header,bool) or header<1:
                raise BridgeError('header_row 必须为正整数')
            result=self.capture(wb.profile,argparse.Namespace(file=str(path),sheet=request.get('sheet'),header_row=header,output=None))
            return json.loads(result)
        if action=='configure':
            path=self.path(request.get('config_path'),suffix='.json')
            blueprint=request.get('blueprint')
            if not isinstance(blueprint,dict):
                raise BridgeError('blueprint 必须为 JSON 对象')
            if path.exists():
                if not path.is_file():
                    raise BridgeError('配置路径已存在且不是文件')
                try:
                    old=json.loads(path.read_text(encoding='utf-8'))
                except (ValueError,UnicodeError):
                    raise BridgeError('拒绝覆盖已有非 JSON 文件')
                if not isinstance(old,dict):
                    raise BridgeError('拒绝覆盖已有非配置 JSON 文件')
            self.sources(blueprint,path.parent)
            path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',dir=path.parent,delete=False) as temp:
                temporary=Path(temp.name)
                json.dump(blueprint,temp,ensure_ascii=False,indent=2)
            try:
                cfg,_=wb.load_blueprint(temporary)
                self.sources(cfg,path.parent)
                temporary.write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            return {'config_path':self.relative(path),'blueprint':cfg}
        if action in ('build','refresh'):
            config,_=self.config(request.get('config_path'))
            output=self.path(request.get('output'))
            if action=='refresh':
                if request.get('full_snapshot') is not True:
                    raise BridgeError('refresh 为完整快照替换，必须明确 full_snapshot=true')
                self.workspace(str(output))
                # Existing symlinks must not let writes/backup escape the assigned root.
                for member in ('blueprint.json','import-report.json'):
                    self.path(str(output/member))
            report=self.path(str(output)+'.validation-errors.json')
            self.capture(wb.materialize,argparse.Namespace(command=action,config=str(config),output=str(output),as_of=request.get('as_of'),report=str(report)))
            return {'workspace':self.relative(output),'report':json.loads((output/'import-report.json').read_text(encoding='utf-8')),
                    'start_command_arguments':[sys.executable,self.relative(output/'runtime/workbench.py'),'serve','--workspace',self.relative(output)],
                    'access_note':'启动地址属于运行宿主；远程聊天用户需宿主提供预览转发，或下载 package 后本机启动。'}
        if action=='inspect':
            path=self.workspace(request.get('workspace'))
            result=wb.state(path)
            return {k:result[k] for k in ('workspace','summary','quality')}
        if action=='package':
            path=self.workspace(request.get('workspace'))
            output=self.path(request.get('output'),suffix='.zip')
            if output.exists():
                raise BridgeError('打包输出已存在，拒绝覆盖')
            names=('index.html','workspace.sqlite','blueprint.json','import-report.json','启动说明.txt','runtime/workbench.py')
            members=[]
            for name in names:
                member=self.path(str(path/name))
                if not member.is_file():
                    raise BridgeError('工作台缺少打包文件 '+name)
                members.append((name,member))
            output.parent.mkdir(parents=True,exist_ok=True)
            # Back up SQLite through its API to produce a coherent downloadable snapshot.
            with tempfile.TemporaryDirectory(prefix='.package-',dir=self.root) as temp:
                backup=Path(temp)/'workspace.sqlite'
                with sqlite3.connect((path/'workspace.sqlite').as_uri()+'?mode=ro',uri=True) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
                staged=Path(temp)/'app.zip'
                with zipfile.ZipFile(staged,'w',zipfile.ZIP_DEFLATED) as archive:
                    for name,member in members:
                        archive.write(backup if name=='workspace.sqlite' else member,'app/'+name)
                # Exclusive create prevents overwriting another process's new artifact.
                with output.open('xb') as destination, staged.open('rb') as source:
                    import shutil
                    shutil.copyfileobj(source,destination)
            return {'package':self.relative(output),'bytes':output.stat().st_size,
                    'contains_user_data':True,'note':'用户自己的工作台下载包，含其数据库；不是可公开分发的空 Skill 包。'}
        raise BridgeError('未实现的 action')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-root',required=True)
    args=parser.parse_args()
    try:
        raw=sys.stdin.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            raise BridgeError('JSON 请求超过 2MiB')
        request=json.loads(raw)
        result=Bridge(args.workspace_root).run(request)
        reply={'ok':True,'result':result}
    except Exception as exc:
        reply={'ok':False,'error':str(exc),'error_type':type(exc).__name__}
    print(json.dumps(reply,ensure_ascii=False,separators=(',',':')))


if __name__=='__main__':
    main()
