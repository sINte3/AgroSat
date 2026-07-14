#!/usr/bin/env python3
"""Hardened, standalone collector for one real Sentinel-2 NDVI field."""
import argparse, json, math, os, re, sys, tempfile, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path: sys.path.insert(0, str(BACKEND))
from services.collector_locking import acquire_lock, release_lock

DEFAULT_LOCK = Path(os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp") / "agrosat_ndvi_collector.lock"
MUTEX = "Global\\AgroSatNdviCollector_v1"
SCHEMA = 1
class ValidationError(ValueError): pass

def sanitize_text(value: Any, limit: int = 1000) -> str:
    text = str(value)[:limit]
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+)", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s,]+", r"\1=[REDACTED]", text)
    text = re.sub(r"\b\w+(?:\+\w+)?://[^\s@]+@[^\s]+", "[REDACTED_URL]", text)
    return re.sub(r"(?i)[a-z]:\\users\\[^\\\s]+", "[REDACTED_USER_PATH]", text)
def sanitize(value: Any) -> Any:
    if isinstance(value, dict): return {sanitize_text(k, 100): sanitize(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [sanitize(v) for v in value]
    return sanitize_text(value) if isinstance(value, str) else value
def external_path(raw: str | None, required=False) -> Path | None:
    if raw is None:
        if required: raise ValidationError("--output-log is required")
        return None
    path = Path(raw)
    if not path.is_absolute(): raise ValidationError("path must be absolute")
    try: path.resolve().relative_to(REPO_ROOT.resolve())
    except ValueError: return path
    raise ValidationError("path must be outside repository")
def dates(a: str|None,b: str|None, lookback: int, today=None):
    if not 1 <= lookback <= 30: raise ValidationError("--lookback-days must be 1..30")
    today=today or date.today()
    try: end=datetime.strptime(b,"%Y-%m-%d").date() if b else today; start=datetime.strptime(a,"%Y-%m-%d").date() if a else end-timedelta(days=lookback)
    except ValueError as exc: raise ValidationError("dates must use YYYY-MM-DD") from exc
    if end>today or start>end or (end-start).days>30: raise ValidationError("date range is invalid or exceeds 30 days")
    return start,end
def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True); fd,tmp=tempfile.mkstemp(prefix="."+path.name,suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(sanitize(data),f,ensure_ascii=False,sort_keys=True); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
def parse_args(argv=None):
    p=argparse.ArgumentParser(description="Collect NDVI for one field; dry-run is safe by default.")
    g=p.add_mutually_exclusive_group(); g.add_argument("--dry-run",action="store_true"); g.add_argument("--apply",action="store_true"); g.add_argument("--write",action="store_true")
    p.add_argument("--field-id",type=int); p.add_argument("--date-from"); p.add_argument("--date-to"); p.add_argument("--lookback-days",type=int,default=14); p.add_argument("--skip-existing",action="store_true"); p.add_argument("--lock-file"); p.add_argument("--output-log")
    return p.parse_args(argv)
def mode(args): return "write" if args.write else "apply" if args.apply else "dry-run"
def field_lookup(field_id:int):
    from database import SessionLocal
    from sqlalchemy import text
    db=SessionLocal()
    try:
        row=db.execute(text("SELECT id, ST_AsText(geometry) AS geometry_wkt FROM fields WHERE id=:id AND is_active=true AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)"),{"id":field_id}).mappings().first()
        if not row: raise ValidationError("field is missing, inactive, or has empty geometry")
        return dict(row)
    finally: db.close()
def persist(field_id:int, record:dict, start:date,end:date):
    from database import SessionLocal
    from sqlalchemy import text
    captured=date.fromisoformat(str(record["captured_date"]))
    if not start<=captured<=end: raise ValidationError("captured date outside requested range")
    db=SessionLocal()
    try:
        existing=db.execute(text("SELECT 1 FROM ndvi_records WHERE field_id=:field_id AND captured_date=:captured_date"),{"field_id":field_id,"captured_date":captured}).first()
        if existing: return False
        values={k:record.get(k) for k in ("mean_ndvi","min_ndvi","max_ndvi","std_ndvi","p10_ndvi","p90_ndvi","cloud_cover_pct","valid_pixels_pct")}; values.update(field_id=field_id,captured_date=captured,satellite="Sentinel-2")
        db.execute(text("INSERT INTO ndvi_records (field_id,captured_date,mean_ndvi,min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,satellite) VALUES (:field_id,:captured_date,:mean_ndvi,:min_ndvi,:max_ndvi,:std_ndvi,:p10_ndvi,:p90_ndvi,:cloud_cover_pct,:valid_pixels_pct,:satellite) ON CONFLICT (field_id,captured_date) DO NOTHING"),values)
        db.commit(); return True
    except Exception:
        db.rollback(); raise
    finally: db.close()
def run(args, *, lookup=field_lookup, service_factory=None, writer=persist):
    started=time.monotonic(); result={"schema_version":SCHEMA,"field_id":args.field_id,"mode":mode(args),"scenes_received":0,"candidate_count":0,"inserted_count":0,"skipped_existing_count":0,"quality_blocked_count":0,"error_count":0,"started_at":datetime.now(timezone.utc).isoformat(),"diagnostics":[]}; lock=None; code=4
    try:
        m=mode(args); start,end=dates(args.date_from,args.date_to,args.lookback_days); result.update(date_from=start.isoformat(),date_to=end.isoformat())
        log=external_path(args.output_log,required=m in ("apply","write")); lockfile=external_path(args.lock_file) or DEFAULT_LOCK
        if m in ("apply","write") and (not args.field_id or args.field_id<=0): raise ValidationError("--apply and --write require --field-id")
        if m=="dry-run": code=0
        else:
            try: lock=acquire_lock(str(lockfile),mutex_name=MUTEX)
            except SystemExit as exc:
                if exc.code==3: code=3; raise RuntimeError("LOCK_CONTENTION")
                raise
            field=lookup(args.field_id)
            if service_factory is None:
                from services.satellite import get_satellite_service
                service_factory=get_satellite_service
            service=service_factory()
            from services.satellite import validate_ndvi_quality
            from services.satellite_safety import require_payload_provenance, require_real_service
            require_real_service(service); observation=service.get_ndvi_stats(field["geometry_wkt"],start,end); result["scenes_received"]=0 if observation is None else 1
            if observation is None: raise RuntimeError("Sentinel collection returned no observation")
            result["candidate_count"]=1; require_payload_provenance(observation)
            valid,_=validate_ndvi_quality(observation.get("mean_ndvi"),observation.get("cloud_cover_pct"),observation.get("min_ndvi"),observation.get("max_ndvi"))
            if not valid: result["quality_blocked_count"]=1
            elif m=="write":
                if writer(args.field_id,observation,start,end): result["inserted_count"]=1
                else: result["skipped_existing_count"]=1
            code=0
    except ValidationError as exc: code=2; result["error_count"]+=1; result["diagnostics"].append(sanitize_text(exc))
    except __import__("services.satellite_safety", fromlist=["SatelliteProvenanceError"]).SatelliteProvenanceError as exc:
        code=2; result["error_count"]+=1; result["diagnostics"].append(sanitize_text(exc))
    except RuntimeError as exc:
        if str(exc)=="LOCK_CONTENTION": pass
        else: code=1; result["error_count"]+=1; result["diagnostics"].append(sanitize_text(exc))
    except Exception as exc: code=4; result["error_count"]+=1; result["diagnostics"].append(sanitize_text(exc))
    finally:
        if lock:
            try: release_lock(lock)
            except Exception as exc: code=4; result["diagnostics"].append(sanitize_text("lock release failed: "+str(exc)))
        result.update(finished_at=datetime.now(timezone.utc).isoformat(),duration_seconds=round(time.monotonic()-started,3),exit_code=code)
        try:
            if 'log' in locals() and log: atomic_json(log,result)
        except Exception:
            code=2; result["exit_code"]=2
    return code
def main(argv=None): raise SystemExit(run(parse_args(argv)))
if __name__=="__main__": main()
