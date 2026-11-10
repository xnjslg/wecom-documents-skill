"""Windows CLI: private JSON jobs and a version-checked local WeCom runtime."""
from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import struct
from datetime import datetime

ROOT=Path(__file__).resolve().parent
def powershell_path() -> Path:
    system_root = os.environ.get('SystemRoot')
    if not system_root:
        raise RuntimeError('SystemRoot is unavailable; run under Windows')
    return Path(system_root) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def run_root() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise RuntimeError('LOCALAPPDATA is unavailable for private document output')
    return Path(local) / 'WeComDocumentTools/runs'


def positive(value: str) -> int:
    number=int(value)
    if number<=0:raise argparse.ArgumentTypeError('Expected a positive integer')
    return number


def gcp_range(value: str) -> dict[str,int]:
    match=re.fullmatch(r'(\d+):(\d+)',value)
    if not match:raise argparse.ArgumentTypeError('Use START:END')
    start,end=map(int,match.groups())
    if end<start or end-start>256:raise argparse.ArgumentTypeError('Expected an ordered range of at most 256 UTF-16 units')
    return {'start':start,'end':end}


def parse_args() -> argparse.Namespace:
    parser=argparse.ArgumentParser(description='Scoped WeCom online Word operations through the current Windows login')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('probe','snapshot','state','locate','apply','refresh','io','upload'):
        p=commands.add_parser(name)
        p.add_argument('--output',type=Path,help='A new output directory; default is a private unique run directory')
        if name in ('snapshot','state','locate','apply','refresh'):
            p.add_argument('--doc-id',required=True)
        if name in ('state','locate','apply','refresh'):
            p.add_argument('--browser-id',type=positive)
        if name=='state':p.add_argument('--range',dest='ranges',type=gcp_range,action='append',default=[])
        if name=='locate':p.add_argument('--text-file',type=Path,required=True)
        if name=='apply':
            p.add_argument('--script',type=Path,required=True,help='Reviewed JavaScript expression, not a historical task script')
            p.add_argument('--expected-version',type=int,required=True)
            p.add_argument('--before-sha256',required=True)
        if name=='io':
            p.add_argument('--operation',choices=['read','create','append','overwrite','import','flowchart_pages'],required=True)
            p.add_argument('--request',type=Path,required=True)
        if name=='upload':
            p.add_argument('--kind',choices=['document-image','media'],required=True)
            p.add_argument('--path',type=Path,required=True)
            p.add_argument('--doc-id')
    args=parser.parse_args()
    if getattr(args,'doc_id',None) and (re.search(r'[\s/?#\\]',args.doc_id) or not args.doc_id):
        parser.error('--doc-id takes the literal document identifier extracted from /doc/<docid>')
    if args.command=='state' and len(args.ranges)>8:parser.error('At most 8 ranges per state query')
    if args.command=='apply':
        if args.expected_version<0:parser.error('--expected-version must be nonnegative')
        if not re.fullmatch(r'[0-9a-fA-F]{64}',args.before_sha256):parser.error('Expected SHA-256 as 64 hexadecimal characters')
    if args.command=='upload' and args.kind=='document-image' and not args.doc_id:
        parser.error('Document image uploads require --doc-id')
    return args


def main() -> int:
    args=parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or sys.version_info < (3, 11):
        raise RuntimeError('Run with 64-bit Windows Python 3.11 or later')
    try:
        frida_version = version('frida')
    except PackageNotFoundError as error:
        raise RuntimeError('Install the bundled runtime/requirements.txt in this Windows Python environment') from error
    if frida_version != '17.25.2':
        raise RuntimeError('This runtime is validated with frida==17.25.2; use the bundled requirements.txt')
    powershell = powershell_path()
    if not powershell.is_file():
        raise RuntimeError('Windows PowerShell is unavailable')
    python = Path(sys.executable).resolve()
    runs = run_root()
    job:dict[str,object]={'command':args.command}
    if getattr(args,'doc_id',None):job['docid']=args.doc_id
    if getattr(args,'browser_id',None):job['browser_id']=args.browser_id
    if args.command=='state':job['ranges']=args.ranges
    if args.command=='locate':
        text=args.text_file.read_text(encoding='utf-8')
        if not text:raise ValueError('Search source must be nonempty')
        job['text']=text
    if args.command=='apply':
        script=args.script.read_text(encoding='utf-8')
        if not script.strip() or len(script.encode('utf-16-le'))//2>16384:
            raise ValueError('Expected a reviewed expression of 1..16384 UTF-16 units')
        job.update(script=script,expected_version=args.expected_version,before_sha256=args.before_sha256.lower())
    if args.command=='io':
        request=json.loads(args.request.read_text(encoding='utf-8'))
        if not isinstance(request,dict):raise ValueError('The native request must be a JSON object')
        job.update(operation=args.operation,request=request)
    if args.command=='upload':
        if not args.path.is_file():raise ValueError('The upload source is not a file')
        job.update(kind=args.kind,file_windows=str(args.path.resolve()))
    runs.mkdir(mode=0o700,parents=True,exist_ok=True)
    if args.output:
        output=args.output.resolve()
        output.mkdir(mode=0o700,parents=True,exist_ok=False)
    else:
        output=Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'),dir=runs))
    subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-File', str(ROOT/'protect_output.ps1'), '-Path', str(output)], check=True)
    job.update(output_windows=str(output),python_windows=str(python))
    job_path=output/'job.json'
    with job_path.open('x',encoding='utf-8') as stream:
        os.chmod(job_path,0o600)
        json.dump(job,stream,ensure_ascii=False,indent=2)
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                           '-File',str(ROOT/'run.ps1'),'-Job',str(job_path)],check=False)
    if result.returncode:
        print(json.dumps({'status':'runner_failed','output_directory':str(output),'exit_code':result.returncode}),file=sys.stderr)
    return result.returncode


if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'error','error_type':type(error).__name__,'message':str(error)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
