"""Run TASK_220 browser checks with the pilot credential confined to child memory."""
import argparse, os, re, subprocess
from pathlib import Path
from dotenv import dotenv_values
SENSITIVE=re.compile(r'(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)')
def main():
    p=argparse.ArgumentParser(); p.add_argument('--pilot-credential-env',type=Path,required=True); p.add_argument('--database-name',required=True); p.add_argument('--cwd',type=Path,required=True); p.add_argument('command',nargs=argparse.REMAINDER); a=p.parse_args()
    if a.database_name=='agrosat' or not a.database_name.startswith('agrosat_r3_task220_'): raise RuntimeError('TASK220_DATABASE_IDENTITY_REJECTED')
    values={k:str(v) for k,v in dotenv_values(a.pilot_credential_env.resolve(strict=True)).items() if v is not None}; emails=[k for k in values if 'EMAIL' in k.upper() or 'USERNAME' in k.upper()]; passwords=[k for k in values if 'PASSWORD' in k.upper()]
    if len(emails)!=1 or len(passwords)!=1 or not a.command: raise RuntimeError('TASK220_BROWSER_CREDENTIAL_CONTRACT_REJECTED')
    env={k:v for k,v in os.environ.items() if not SENSITIVE.search(k)}; env.update({'TASK220_BROWSER_USERNAME':values[emails[0]],'TASK220_BROWSER_PASSWORD':values[passwords[0]],'TASK220_BROWSER_DATABASE_IDENTITY':a.database_name})
    done=subprocess.run(a.command,cwd=a.cwd.resolve(strict=True),env=env,stdin=subprocess.DEVNULL); env.clear(); values.clear(); return done.returncode
if __name__=='__main__': raise SystemExit(main())
