#!/usr/bin/env python3
"""Generate a self-contained instruction file for hosts without skill-directory access."""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('SKILL.md', 'references/intake.md', 'references/framework.md',
           'references/data-contract.md', 'references/runtime.md', 'references/platforms.md')

def render():
    sections = [
        '# 业务工作台生成器 · 通用 Agent 指令\n',
        '本文件由同包 scripts/export_prompt.py 从 Skill 入口与参考文档生成。更新时修改源文件后重新生成，避免多平台口径分叉。\n',
        '使用方式：将本文件设为智能体指令、按平台能力作为知识文件提供，或直接粘贴到新对话。仅上传/粘贴文字不会使聊天产品获得文件执行、数据库或部署权限。不要把下文的工具步骤当作已经执行。\n',
        '若只收到本文件：它包含业务工作流与字段契约；运行时还需要完整包中的 scripts/、assets/，或宿主已接好的 service_workbench 工具。不要要求用户提供原作者数据库。\n'
    ]
    for source in SOURCES:
        text = (ROOT / source).read_text(encoding='utf-8')
        if source == 'SKILL.md':
            text = re.sub(r'\A---\n.*?\n---\n', '', text, count=1, flags=re.S)
        text = re.sub(r'\[([^\]]+)\]\((?!https?://)[^)]+\.md\)', r'《\1》（内容附于本指令）', text)
        sections.append('\n---\n\n<!-- source: ' + source + ' -->\n\n' + text.strip() + '\n')
    return '\n'.join(sections)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='Output Markdown path; otherwise print to stdout')
    args = parser.parse_args()
    result = render()
    if args.output:
        target = Path(args.output)
        if target.resolve() in { (ROOT/source).resolve() for source in SOURCES }:
            parser.error('输出文件不能覆盖源指令')
        target.write_text(result,encoding='utf-8')
        print(str(target.resolve()))
    else:
        sys.stdout.write(result)

if __name__ == '__main__':
    main()
