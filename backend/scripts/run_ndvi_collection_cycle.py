#!/usr/bin/env python3
"""Bounded rotating process runner for the standalone NDVI collector."""
import argparse,json,os,re,subprocess,sys,tempfile,time,uuid
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Callable
BACKEND=Path(__file__).resolve().parents[1]; REPO_ROOT=BACKEND.parent
if str(BACKEND) not in sys.path: sys.path.insert(0,str(BACKEND))
from services.collector_locking import acquire_lock,release_lock
SCHEMA=1; MAX_BATCH=100; MAX_DRY=25; MAX_ATTEMPTS=5; MAX_BACKOFF=300; CAPTURE=16384
DEFAULT_LOCK=Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp")/"agrosat_ndvi_cycle.lock"; MUTEX="Global\\AgroSatNdviCollectionCycle_v1"
class ValidationError(ValueError): pass
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def sanitize_text(v,limit=4000):
 s=str(v)[:limit]; s=re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)",r"\1[REDACTED]",s); s=re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+",r"\1=[REDACTED]",s); return re.sub(r"\b\w+(?:\+\w+)?://[^\s@]+@[^\s]+","[REDACTED_URL]",s)
def sanitize(v):
 if isinstance(v,dict): return {sanitize_text(k,200):sanitize(x) for k,x in v.items()}
 if isinstance(v,(list,tuple)): return [sanitize(x) for x in v]
 return sanitize_text(v) if isinstance(v,str) else v
def external(raw,label,required=False):
 if raw is None:
  if required: raise ValidationError(label+" is required")
  return None
 p=Path(raw)
 if not p.is_absolute(): raise ValidationError(label+" must be an absolute path")
 try:p.resolve().relative_to(REPO_ROOT.resolve())
 except ValueError:return p
 raise ValidationError(label+" must be outside repository")
def resolve_dates(a,b,days,today=None):
 if not 1<=days<=30: raise ValidationError("--lookback-days must be 1..30")
 today=today or date.today()
 try: end=datetime.strptime(b,"%Y-%m-%d").date() if b else today; start=datetime.strptime(a,"%Y-%m-%d").date() if a else end-timedelta(days=days)
 except ValueError as e: raise ValidationError("dates must use YYYY-MM-DD") from e
 if end>today or start>end or (end-start).days>30: raise ValidationError("date range is invalid or exceeds 30 days")
 return start,end
def parse_ids(raw):
 vals=[]
 for p in raw.split(','):
  if not p.strip():continue
  try:n=int(p.strip())
  except ValueError as e:raise ValidationError("--field-ids must contain positive integers") from e
  if n<=0:raise ValidationError("--field-ids must contain positive integers")
  if n not in vals:vals.append(n)
 if not vals or len(vals)>MAX_BATCH:raise ValidationError("--field-ids must contain 1 to 100 unique positive integers")
 return vals
def state_default():return {"schema_version":1,"next_offset":0,"retry_field_ids":[],"last_completed_run_id":None,"updated_at":None}
def validate_state(s):
 if not isinstance(s,dict) or set(s)!=set(state_default()) or s["schema_version"]!=1 or not isinstance(s["next_offset"],int) or s["next_offset"]<0 or not isinstance(s["retry_field_ids"],list) or len(s["retry_field_ids"])>MAX_BATCH or any(type(x)is not int or x<=0 for x in s["retry_field_ids"]) or len(set(s["retry_field_ids"]))!=len(s["retry_field_ids"]) or s["last_completed_run_id"] is not None and not isinstance(s["last_completed_run_id"],str) or s["updated_at"] is not None and not isinstance(s["updated_at"],str):raise ValidationError("state schema is invalid")
 return s
def load_state(p):
 if not p.exists():return state_default()
 try:return validate_state(json.loads(p.read_text(encoding="utf-8")))
 except Exception as e:raise ValidationError("state file is malformed") from e
def atomic_json(p,data):
 p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix="."+p.name,suffix=".tmp",dir=p.parent)
 try:
  with os.fdopen(fd,"w",encoding="utf-8") as f:json.dump(sanitize(data),f,ensure_ascii=False,sort_keys=True);f.flush();os.fsync(f.fileno())
  os.replace(tmp,p)
 except Exception:
  try:os.unlink(tmp)
  except OSError:pass
  raise
def select_batch(active,state,size):
 active=list(dict.fromkeys(active)); aset=set(active)
 if not active:return [],0,0,0
 retries=[x for x in state["retry_field_ids"] if x in aset]; cap=size if len(retries)==len(active) else min(size//2,size-1); retry=retries[:cap]; off=state["next_offset"]%len(active); rotated=active[off:]+active[:off]; rotation=[x for x in rotated if x not in retries][:size-len(retry)]
 scan=0;needed=len(rotation)
 for x in rotated:
  scan+=1
  if x not in retries:
   needed-=1
   if not needed:break
 return retry+rotation,len(retry),len(rotation),(off+scan)%len(active)
def query_active():
 from database import SessionLocal
 from sqlalchemy import text
 db=SessionLocal()
 try:return [int(r[0]) for r in db.execute(text("SELECT id FROM fields WHERE is_active=true AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry) ORDER BY id ASC")).all()]
 finally:db.close()
def command(fid,start,end,mode,log,lock):return [sys.executable,str((BACKEND/"scripts"/"collect_ndvi.py").resolve()),"--field-id",str(fid),"--date-from",start.isoformat(),"--date-to",end.isoformat(),"--skip-existing","--output-log",str(log),"--lock-file",str(lock),"--"+mode]
def execute(cmd,timeout):
 out=tempfile.NamedTemporaryFile(prefix="agrosat_ndvi_out_",delete=False);err=tempfile.NamedTemporaryFile(prefix="agrosat_ndvi_err_",delete=False);op,ep=Path(out.name),Path(err.name)
 try:
  p=subprocess.Popen(cmd,cwd=str(REPO_ROOT),shell=False,stdout=out,stderr=err);out.close();err.close();timed=False
  try:p.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
   timed=True;p.terminate()
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.kill();p.wait()
  return {"exit_code":1 if timed else p.returncode,"timed_out":timed,"stdout":op.read_bytes()[:CAPTURE+1].decode("utf-8",errors="replace")[:CAPTURE],"stderr":ep.read_bytes()[:CAPTURE+1].decode("utf-8",errors="replace")[:CAPTURE]}
 finally:
  for h in(out,err):
   if not h.closed:h.close()
  for p in(op,ep):
   try:p.unlink()
   except OSError:pass
def parse_child(p):
 try:d=json.loads(p.read_text(encoding="utf-8"))
 except Exception as e:raise ValidationError("child JSON summary is missing or malformed") from e
 required={"exit_code","inserted_count","skipped_existing_count","quality_blocked_count"}
 if not isinstance(d,dict) or not required<=set(d) or not isinstance(d["exit_code"],int):raise ValidationError("child JSON summary is malformed")
 return sanitize(d)
def parse_args(argv=None):
 p=argparse.ArgumentParser(description="Run bounded rotating standalone NDVI collection.");g=p.add_mutually_exclusive_group();g.add_argument("--dry-run",action="store_true");g.add_argument("--apply",action="store_true");g.add_argument("--write",action="store_true");s=p.add_mutually_exclusive_group();s.add_argument("--field-ids");s.add_argument("--all-active-fields",action="store_true")
 p.add_argument("--batch-size",type=int);p.add_argument("--max-fields",type=int);p.add_argument("--state-file");p.add_argument("--lock-file");p.add_argument("--output-dir");p.add_argument("--date-from");p.add_argument("--date-to");p.add_argument("--lookback-days",type=int,default=14);p.add_argument("--max-attempts",type=int,default=3);p.add_argument("--retry-base-seconds",type=int,default=2);p.add_argument("--field-timeout-seconds",type=int,default=180);return p.parse_args(argv)
def mode(a):return "write" if a.write else "apply" if a.apply else "dry-run"
def validate(a):
 m=mode(a)
 if not 1<=a.max_attempts<=MAX_ATTEMPTS or not 0<=a.retry_base_seconds<=MAX_BACKOFF or not 1<=a.field_timeout_seconds<=900:raise ValidationError("retry or timeout bounds are invalid")
 if a.field_ids:parse_ids(a.field_ids)
 if not(a.field_ids or a.all_active_fields) and not(m=="dry-run" and a.max_fields):raise ValidationError("an explicit scope or bounded dry-run --max-fields is required")
 if a.all_active_fields and (a.batch_size is None or not 2<=a.batch_size<=MAX_BATCH):raise ValidationError("--all-active-fields requires --batch-size between 2 and 100")
 if a.max_fields is not None and (not 1<=a.max_fields<=MAX_DRY or m!="dry-run"):raise ValidationError("--max-fields is dry-run only and must be 1..25")
 return m,external(a.state_file,"--state-file",a.all_active_fields and m!="dry-run"),external(a.output_dir,"--output-dir",m!="dry-run"),external(a.lock_file,"--lock-file")
def run(a,*,field_query=query_active,child_runner=execute,sleeper=time.sleep):
 started=time.monotonic();rid=uuid.uuid4().hex;code=4;lock=None;run_dir=None;state=state_default();next_state=None;results=[];summary={"schema_version":1,"run_id":rid,"exit_code":4,"started_at":now(),"diagnostics":[]}
 try:
  m,state_file,out_dir,lock_override=validate(a);start,end=resolve_dates(a.date_from,a.date_to,a.lookback_days)
  if out_dir:run_dir=out_dir/("cycle_"+rid);(run_dir/"per_field").mkdir(parents=True,exist_ok=False)
  try:lock=acquire_lock(str(lock_override or DEFAULT_LOCK),mutex_name=MUTEX)
  except SystemExit as e:
   if e.code==3:code=3;raise RuntimeError("LOCK_CONTENTION")
   raise
  state=load_state(state_file) if state_file else state_default()
  if a.field_ids:selected=parse_ids(a.field_ids);rc=rot=0;offset=state["next_offset"]
  else:selected,rc,rot,offset=select_batch(field_query(),state,a.batch_size or a.max_fields or MAX_DRY)
  if a.max_fields:selected=selected[:a.max_fields]
  if not selected:raise ValidationError("NO_ACTIVE_FIELDS_OR_EMPTY_BATCH")
  failed=[];fatal=False;tot={"attempts":0,"timeouts":0,"inserted":0,"skipped":0,"blocked":0}
  for fid in selected:
   ok=False
   for attempt in range(1,a.max_attempts+1):
    temp=run_dir is None
    if run_dir:log=run_dir/"per_field"/(f"{fid}_attempt_{attempt}.json")
    else:fd,n=tempfile.mkstemp(suffix=".json");os.close(fd);log=Path(n)
    try:
     outcome=sanitize(child_runner(command(fid,start,end,m,log,Path(str(lock_override or DEFAULT_LOCK)+f".child.{fid}")),a.field_timeout_seconds));tot["attempts"]+=1;tot["timeouts"]+=int(bool(outcome.get("timed_out")));record={"field_id":fid,"attempt":attempt,**outcome}
     child=parse_child(log)
     if child["exit_code"] != int(record.get("exit_code",4)): raise ValidationError("child process and summary exit codes differ")
     record["child_summary"]=child;tot["inserted"]+=int(child["inserted_count"]);tot["skipped"]+=int(child["skipped_existing_count"]);tot["blocked"]+=int(child["quality_blocked_count"]);c=int(record["exit_code"]);record["classification"]="SUCCESS" if c==0 else "FATAL_CHILD_CONTRACT_ERROR" if c==2 else "RETRYABLE";results.append(record)
     if c==0:ok=True;break
     if c==2:fatal=True;break
     if attempt<a.max_attempts:sleeper(min(a.retry_base_seconds*2**(attempt-1),MAX_BACKOFF))
    except ValidationError as e:results.append({"field_id":fid,"attempt":attempt,"exit_code":2,"diagnostic":sanitize_text(e)});fatal=True;break
    finally:
     if temp:
      try:log.unlink()
      except OSError:pass
   if fatal:break
   if not ok:failed.append(fid)
  retry=[x for x in state["retry_field_ids"] if x not in selected and x not in failed]+[x for x in failed if x not in state["retry_field_ids"]];retry=retry[:MAX_BATCH];code=2 if fatal else 1 if failed else 0
  summary.update(mode=m,date_from=start.isoformat(),date_to=end.isoformat(),selected_field_ids=selected,batch_source_counts={"retry":rc,"rotation":rot},attempt_counts=tot["attempts"],success_count=len(selected)-len(failed),failure_count=len(failed),timeout_count=tot["timeouts"],inserted_count=tot["inserted"],skipped_existing_count=tot["skipped"],quality_blocked_count=tot["blocked"],retry_queue_before=state["retry_field_ids"],retry_queue_after=retry,state_advanced=False)
  if not fatal and state_file:next_state={"schema_version":1,"next_offset":offset,"retry_field_ids":retry,"last_completed_run_id":rid,"updated_at":now()}
 except ValidationError as e:code=2;summary["diagnostics"].append(sanitize_text(e))
 except RuntimeError as e:
  if str(e)!="LOCK_CONTENTION":code=4;summary["diagnostics"].append(sanitize_text(e))
 except Exception as e:code=4;summary["diagnostics"].append(sanitize_text(e))
 finally:
  summary.update(finished_at=now(),duration_seconds=round(time.monotonic()-started,3))
  if run_dir:
   try:
    with (run_dir/"field_results.jsonl").open("w",encoding="utf-8") as f:
     for r in results:f.write(json.dumps(sanitize(r),ensure_ascii=False)+"\n")
    summary["exit_code"]=code;atomic_json(run_dir/"cycle_summary.json",summary)
    if next_state and code in(0,1):atomic_json(state_file,next_state);summary["state_advanced"]=True
   except Exception as e:code=2;summary["diagnostics"].append(sanitize_text(e));summary["exit_code"]=code
   try:atomic_json(run_dir/"cycle_summary.json",summary)
   except Exception:code=2
  if lock:
   try:release_lock(lock)
   except Exception as e:code=4;summary["diagnostics"].append(sanitize_text(e))
  if run_dir:
   summary["exit_code"]=code
   try:atomic_json(run_dir/"cycle_summary.json",summary)
   except Exception:code=2
 return code
def main(argv=None):raise SystemExit(run(parse_args(argv)))
if __name__=="__main__":main()
