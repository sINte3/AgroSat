"""Run a TASK_220 child with approved credentials confined to child memory."""
import argparse, os, re, subprocess
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

PREFIX='agrosat_r3_task220_'; SENSITIVE=re.compile(r'(?i)(secret|token|password|api.?key|database.?url|authorization|cookie|pgpass)')
def main():
    p=argparse.ArgumentParser(); p.add_argument('--source-runtime-env',type=Path,required=True); p.add_argument('--database-name',required=True); p.add_argument('--media-root',type=Path,required=True); p.add_argument('--cache-root',type=Path,required=True); p.add_argument('--cwd',type=Path,required=True); p.add_argument('command',nargs=argparse.REMAINDER); a=p.parse_args()
    if a.database_name=='agrosat' or not a.database_name.startswith(PREFIX) or not a.database_name.removeprefix(PREFIX).replace('_','').isalnum(): raise RuntimeError('TASK220_DATABASE_IDENTITY_REJECTED')
    if not a.command: raise RuntimeError('TASK220_CHILD_COMMAND_MISSING')
    values={k:str(v) for k,v in dotenv_values(a.source_runtime_env.resolve(strict=True)).items() if v is not None}; source=make_url(values.get('DATABASE_URL') or values.get('SUPABASE_DATABASE_URL') or '')
    if source.database!='agrosat': raise RuntimeError('TASK220_SOURCE_DATABASE_IDENTITY_REJECTED')
    environment={k:v for k,v in os.environ.items() if not SENSITIVE.search(k)}; environment.update(values); environment.update({'DATABASE_URL':source.set(database=a.database_name).render_as_string(hide_password=False),'AGROSAT_RUNTIME_ENV_FILE':str(a.source_runtime_env.resolve(strict=True)),'ENVIRONMENT':'development','DEBUG':'false','PUBLIC_REGISTRATION_ENABLED':'false','WIALON_ENABLED':'false','TELEGRAM_NOTIFICATIONS_ENABLED':'false','INSPECTION_MEDIA_DIRECTORY':str(a.media_root.resolve()),'PIXEL_NDVI_CACHE_DIRECTORY':str(a.cache_root.resolve()),'RELEASE_REVISION':os.environ.get('AGROSAT_RELEASE_COMMIT','f'*40),'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(a.cwd.resolve(strict=True))})
    a.media_root.mkdir(parents=True,exist_ok=True); a.cache_root.mkdir(parents=True,exist_ok=True)
    done=subprocess.run(a.command,cwd=a.cwd.resolve(strict=True),env=environment,stdin=subprocess.DEVNULL); values.clear(); environment.clear(); return done.returncode
if __name__=='__main__': raise SystemExit(main())
