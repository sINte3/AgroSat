#!/usr/bin/env python3
"""Restore a verified accepted backup into a protected TASK_220 runtime database."""
import argparse, hashlib, os, re, subprocess, sys
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SAFE=re.compile(r'^agrosat_r3_task220_[a-z0-9_]+$')
def main():
    p=argparse.ArgumentParser(); p.add_argument('--database',required=True); p.add_argument('--backup',type=Path,required=True); p.add_argument('--expected-sha256',required=True); p.add_argument('--backend',type=Path,required=True); p.add_argument('--runtime-env',type=Path,required=True); p.add_argument('--pg-bin',type=Path,required=True); a=p.parse_args()
    if a.database=='agrosat' or not SAFE.fullmatch(a.database): raise RuntimeError('TASK220_DATABASE_IDENTITY_REJECTED')
    backup=a.backup.resolve(strict=True)
    if hashlib.sha256(backup.read_bytes()).hexdigest()!=a.expected_sha256.lower(): raise RuntimeError('TASK220_BACKUP_HASH_REJECTED')
    os.environ['AGROSAT_RUNTIME_ENV_FILE']=str(a.runtime_env.resolve(strict=True)); backend=a.backend.resolve(strict=True); sys.path.insert(0,str(backend))
    from config import settings
    source=make_url(settings.database_url)
    if source.database!='agrosat': raise RuntimeError('TASK220_SOURCE_IDENTITY_REJECTED')
    admin=create_engine(source.set(database='postgres'),isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:n AND pid<>pg_backend_pid()'),{'n':a.database}); c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{a.database}"'); c.exec_driver_sql(f'CREATE DATABASE "{a.database}"')
    env=os.environ.copy(); env.update({'PGHOST':source.host or 'localhost','PGPORT':str(source.port or 5432),'PGUSER':source.username or '','PGPASSWORD':source.password or '','PGDATABASE':a.database})
    restore=subprocess.run([str(a.pg_bin.resolve(strict=True)/'pg_restore.exe'),'--exit-on-error','--no-owner','--no-privileges','--dbname',a.database,str(backup)],env=env,capture_output=True,text=True,timeout=900)
    if restore.returncode: raise RuntimeError('TASK220_RESTORE_FAILED:'+restore.stderr[-2000:])
    env['DATABASE_URL']=source.set(database=a.database).render_as_string(hide_password=False); env.pop('AGROSAT_RUNTIME_ENV_FILE',None)
    migration=subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head'],cwd=backend,env=env,capture_output=True,text=True,timeout=900)
    if migration.returncode: raise RuntimeError('TASK220_UPGRADE_FAILED:'+migration.stderr[-2000:])
    engine=create_engine(source.set(database=a.database))
    with engine.connect() as c:
        revision=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(); enterprises=c.execute(text('SELECT count(*) FROM enterprises')).scalar_one(); fields=c.execute(text('SELECT count(*) FROM fields')).scalar_one(); users=c.execute(text('SELECT count(*) FROM users')).scalar_one()
    if revision!='0015_closed_loop_agronomy' or min(enterprises,fields,users)<1: raise RuntimeError('TASK220_RUNTIME_BASELINE_REJECTED')
    print({'status':'PASS','database':a.database,'revision':revision,'counts':{'enterprises':enterprises,'fields':fields,'users':users},'backup_sha256':a.expected_sha256.lower()}); return 0
if __name__=='__main__': raise SystemExit(main())
