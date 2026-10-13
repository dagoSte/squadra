"""Run the same assertions on isolated original, corrected and valid code."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from registry import Error, atomic, canonical, digest, git, now, require, snapshot
from runtime import terminate

WRAPPER = '''import importlib.util,json,sys,traceback
from pathlib import Path
spec=importlib.util.spec_from_file_location("squadra_oracle",sys.argv[1])
try:
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 module.check(Path.cwd())
except AssertionError as exc:
 traceback.print_exc();Path(sys.argv[2]).write_text(json.dumps({"outcome":"assertion","error":str(exc)}));sys.exit(1)
except BaseException as exc:
 traceback.print_exc();Path(sys.argv[2]).write_text(json.dumps({"outcome":"instrument-error","error":type(exc).__name__}));sys.exit(2)
else:
 Path(sys.argv[2]).write_text(json.dumps({"outcome":"passed"}));sys.exit(0)
'''


def run(oracle, original, remedy, positive, destination, timeout=30):
    require(os.name != "nt", "Windows challenge process ownership not qualified")
    require(type(timeout) is int and 1 <= timeout <= 3600, "Bounded timeout required")
    oracle=Path(oracle).resolve(strict=True);body=oracle.read_bytes()
    folder=Path(destination).resolve();require(not folder.exists(),"Challenge destination must be new")
    roots=[Path(p).resolve(strict=True) for p in (original,remedy,positive)]
    require(not any(folder==r or r in folder.parents for r in roots),"Challenge output outside sources")
    for repo in roots:require(not git(repo,"status","--porcelain").strip(),"Commit a clean exact challenge candidate")
    folder.mkdir(parents=True,mode=0o700)
    frozen=folder/'oracle.py';frozen.write_bytes(body);(folder/'wrapper.py').write_text(WRAPPER)
    result={"status":"failed","utc":now(),"oracle_sha256":digest(body),"runs":[],
            "scope":"One assertion challenge only; no delivery proof or general security acceptance"}
    try:
        for label,repo in zip(('original','remedy','positive'),roots):
            before=snapshot(repo);work=folder/label;work.mkdir(mode=0o700)
            archive=folder/(label+'.tar');archive.write_bytes(git(repo,'archive','--format=tar','HEAD'))
            # Git archives contain tracked paths; refuse path/symlink escapes before extraction.
            import tarfile
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    path=Path(member.name)
                    require(not path.is_absolute() and '..' not in path.parts and not member.islnk(),"Unsafe Git archive")
                    if member.issym():
                        require(not Path(member.linkname).is_absolute() and ((work/member.name).parent/member.linkname).resolve().is_relative_to(work),"Unsafe archive symlink")
                tar.extractall(work,filter='data')
            from recovery import inventory
            executed=digest(canonical(inventory(work)).encode())
            output=folder/(label+'.result.json')
            argv=[sys.executable,'-B',str(folder/'wrapper.py'),str(frozen),str(output)]
            started=time.monotonic();status='completed';env=os.environ|{'PYTHONDONTWRITEBYTECODE':'1'}
            home=folder/(label+'-home');home.mkdir();env['HOME']=str(home);env['TMPDIR']=str(home)
            with (folder/(label+'.stdout.log')).open('wb') as out,(folder/(label+'.stderr.log')).open('wb') as err:
                proc=subprocess.Popen(argv,cwd=work,env=env,stdout=out,stderr=err,start_new_session=True)
                try:rc=proc.wait(timeout=timeout)
                except (subprocess.TimeoutExpired,KeyboardInterrupt):status='interrupted';rc=None
                finally:
                    # Dispose inherited descendants even if the leader finished first.
                    try:terminate(proc)
                    except Exception as exc:
                        status='cleanup-unknown'
                        observed_cleanup=str(exc)
                        try:proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:pass
                    rc=proc.poll()
            observed=json.loads(output.read_text()) if output.is_file() else {'outcome':'instrument-error'}
            require(snapshot(repo)==before,'Original source changed during challenge')
            require(digest(canonical(inventory(work)).encode())==executed,'Oracle changed archived input bytes')
            result['runs'].append({'label':label,'head':before['head'],'snapshot':before['sha256'],
                                  'executed_sha256':executed,
                                  'argv':argv,'native_rc':rc,'status':status,'seconds':round(time.monotonic()-started,3),**observed})
            if status=='cleanup-unknown':result['runs'][-1]['cleanup_error']=observed_cleanup
        outcomes={r['label']:r for r in result['runs']}
        if (outcomes['original']['outcome']=='assertion' and outcomes['original']['native_rc']==1 and
                all(outcomes[k]['outcome']=='passed' and outcomes[k]['native_rc']==0 for k in ('remedy','positive')) and
                all(r['status']=='completed' for r in result['runs'])):
            result['status']='matched'
        return result
    finally:
        atomic(folder/'RESULT.json',(canonical(result)+'\n').encode())
