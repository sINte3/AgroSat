#!/usr/bin/env python3
"""Fail-closed fresh/migration/dump/restore qualification for TASK_220."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, sys
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

SAFE = re.compile(r'^agrosat_r3_task220_[a-z0-9_]+$')
HEAD = '0015_closed_loop_agronomy'
TABLES = ('agronomy_plans','agronomy_work_items','agronomy_verifications','agronomy_events')

def safe(name):
    if name == 'agrosat' or not SAFE.fullmatch(name): raise RuntimeError('TASK220_DATABASE_IDENTITY_REJECTED')
    return name

def quote(name): return '"' + safe(name).replace('"','""') + '"'
def sanitize(value):
    value = re.sub(r'(?i)(postgres(?:ql)?://)[^\s]+', r'\1[REDACTED]', value or '')
    return re.sub(r'(?i)(password|token|secret|authorization)=\S+', r'\1=[REDACTED]', value)[-8000:]

def child(command, *, cwd, env, timeout=900):
    done=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,timeout=timeout)
    result={'command':[Path(command[0]).name,*command[1:]],'exit_code':done.returncode,'stdout':sanitize(done.stdout),'stderr':sanitize(done.stderr)}
    if done.returncode: raise RuntimeError(f"command failed: {result}")
    return result

def main():
    p=argparse.ArgumentParser(); p.add_argument('--database',required=True); p.add_argument('--restore-database',required=True)
    p.add_argument('--backend',type=Path,required=True); p.add_argument('--runtime-env',type=Path,required=True)
    p.add_argument('--pg-bin',type=Path,required=True); p.add_argument('--dump',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); database=safe(a.database); restored=safe(a.restore_database); backend=a.backend.resolve(strict=True)
    os.environ['AGROSAT_RUNTIME_ENV_FILE']=str(a.runtime_env.resolve(strict=True)); sys.path.insert(0,str(backend))
    from config import settings
    source=make_url(settings.database_url)
    if source.database!='agrosat': raise RuntimeError('TASK220_SOURCE_DATABASE_IDENTITY_REJECTED')
    admin=create_engine(source.set(database='postgres'),isolation_level='AUTOCOMMIT',pool_pre_ping=True)
    with admin.connect() as c:
        for name in (database,restored):
            safe(name); c.execute(text('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:n AND pid<>pg_backend_pid()'),{'n':name})
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS {quote(name)}'); c.exec_driver_sql(f'CREATE DATABASE {quote(name)}')
    target=source.set(database=database); env=os.environ.copy(); env['DATABASE_URL']=target.render_as_string(hide_password=False); env.pop('AGROSAT_RUNTIME_ENV_FILE',None)
    engine=create_engine(target,pool_pre_ping=True)
    with engine.begin() as c: c.execute(text('CREATE EXTENSION IF NOT EXISTS postgis'))
    commands=[]
    for args in (('heads',),('upgrade','head'),('current',),('check',),('downgrade','0014_autonomous_satellite_monitoring'),('upgrade','head')):
        commands.append(child([sys.executable,'-m','alembic','-c','alembic.ini',*args],cwd=backend,env=env))
    import models.registry
    from database import Base
    inspector=inspect(engine)
    metadata={name:sorted(col.name for col in Base.metadata.tables[name].columns) for name in TABLES}
    actual={name:sorted(col['name'] for col in inspector.get_columns(name)) for name in TABLES}
    if metadata!=actual: raise RuntimeError(f'schema metadata mismatch: expected={metadata} actual={actual}')
    with engine.connect() as c:
        revision=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        invalid=c.execute(text('SELECT count(*) FROM pg_index WHERE NOT indisvalid')).scalar_one()
        srid=c.execute(text("SELECT srid,type FROM geometry_columns WHERE f_table_name='agronomy_work_items' AND f_geometry_column='geometry'" )).mappings().one()
        constraints=c.execute(text("SELECT count(*) FROM pg_constraint WHERE convalidated=false")).scalar_one()
        indexes=c.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname IN ('ix_agronomy_work_geometry','uq_agronomy_plan_active_inspection','ix_agronomy_event_timeline')")).scalar_one()
    if revision!=HEAD or invalid or constraints or srid['srid']!=4326 or indexes!=3: raise RuntimeError('fresh schema integrity failed')
    a.dump.parent.mkdir(parents=True,exist_ok=True); pg_env=env.copy(); pg_env.update({'PGHOST':source.host or 'localhost','PGPORT':str(source.port or 5432),'PGUSER':source.username or '', 'PGPASSWORD':source.password or '', 'PGDATABASE':database})
    dump=child([str(a.pg_bin.resolve(strict=True)/'pg_dump.exe'),'-Fc','--no-owner','--no-privileges','-f',str(a.dump),database],cwd=backend,env=pg_env)
    listing=child([str(a.pg_bin.resolve(strict=True)/'pg_restore.exe'),'--list',str(a.dump)],cwd=backend,env=pg_env)
    restore_env={**pg_env,'PGDATABASE':restored}
    restore=child([str(a.pg_bin.resolve(strict=True)/'pg_restore.exe'),'--exit-on-error','--no-owner','--no-privileges','--dbname',restored,str(a.dump)],cwd=backend,env=restore_env)
    restored_engine=create_engine(source.set(database=restored),pool_pre_ping=True)
    with restored_engine.connect() as c:
        restored_revision=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        restored_invalid=c.execute(text('SELECT count(*) FROM pg_index WHERE NOT indisvalid')).scalar_one()
        restored_tables=c.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=ANY(:names)"),{'names':list(TABLES)}).scalar_one()
    engine.dispose(); restored_engine.dispose(); admin.dispose()
    result={'status':'PASS','database':database,'restore_database':restored,'head':revision,'restored_head':restored_revision,'alembic_commands':commands,'metadata_columns_match':True,'postgis':dict(srid),'invalid_indexes':invalid,'invalid_constraints':constraints,'required_indexes':indexes,'dump_sha256':hashlib.sha256(a.dump.read_bytes()).hexdigest(),'pg_restore_list_entries':len(listing['stdout'].splitlines()),'restore_table_count':restored_tables,'restore_invalid_indexes':restored_invalid,'database_urls_included':False,'databases_retained':True}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('status','database','restore_database','head','restored_head','dump_sha256')})); return 0

if __name__=='__main__': raise SystemExit(main())
