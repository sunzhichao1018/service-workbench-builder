#!/usr/bin/env python3
"""Install this directory bundle into an explicitly selected host skill directory.
Only hosts with filesystem skill loading can use this installer; no model APIs are called.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = 'service-workbench-builder'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skills-dir',required=True,help='A skill root actually loaded by the target host')
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    try:
        parent=Path(args.skills_dir).expanduser().resolve()
        target=parent/NAME
        if target==ROOT or ROOT in target.parents or target in ROOT.parents:
            raise ValueError('安装目标不能与当前 Skill 目录重叠')
        if target.exists():
            raise ValueError('目标已存在，未覆盖；请在核对后选择新位置或自行管理更新')
        for relative in ('SKILL.md','scripts/workbench.py','assets/workbench.html'):
            if not (ROOT/relative).is_file():
                raise ValueError('来源 Skill 不完整：'+relative)
        result={'ok':True,'target':str(target),'dry_run':args.dry_run,
                'note':'仅复制通用技能目录；宿主加载、工具授权和模型配置需由该平台管理。'}
        if not args.dry_run:
            parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(ROOT,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store'))
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())
