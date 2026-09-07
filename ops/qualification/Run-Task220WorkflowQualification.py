#!/usr/bin/env python3
"""Real PostgreSQL-backed HTTP lifecycle qualification for TASK_220."""
from datetime import date, datetime, timedelta, timezone
import argparse, json, logging, sys, uuid
from pathlib import Path
from types import SimpleNamespace
from fastapi.testclient import TestClient
from sqlalchemy import text

PNG=(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82')

def require(response,status,label):
    if response.status_code!=status: raise AssertionError(f'{label}: {response.status_code} {response.text[:500]}')
    return response.json() if response.content else None

def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
    backend=Path(__file__).resolve().parents[2]/'backend'; sys.path.insert(0,str(backend))
    from api.auth import get_current_active_user
    from database import engine
    from main import app
    if engine.url.database=='agrosat' or not str(engine.url.database).startswith('agrosat_r3_task220_'): raise RuntimeError('TASK220_DATABASE_IDENTITY_REJECTED')
    logging.getLogger('httpx').setLevel(logging.WARNING); tag=uuid.uuid4().hex; today=date.today()
    with engine.begin() as c:
        fields=c.execute(text('SELECT f.id,f.enterprise_id FROM fields f WHERE NOT EXISTS (SELECT 1 FROM ndvi_records n WHERE n.field_id=f.id AND n.captured_date=CURRENT_DATE) ORDER BY f.id')).mappings().all(); first=fields[0]; other=next(x for x in fields if x['enterprise_id']!=first['enterprise_id'])
        inside_geometry=c.execute(text('SELECT ST_AsGeoJSON(ST_PointOnSurface(geometry))::json FROM fields WHERE id=:id'),{'id':first['id']}).scalar_one()
        seed=c.execute(text('SELECT id,hashed_password FROM users ORDER BY id LIMIT 1')).mappings().one()
        def user(email,role,enterprise):
            return c.execute(text("INSERT INTO users(enterprise_id,email,full_name,role,hashed_password,is_active,created_at) VALUES(:e,:mail,:name,:role,:hash,true,now()) RETURNING id"),{'e':enterprise,'mail':email,'name':'TASK 220 fixture','role':role,'hash':seed['hashed_password']}).scalar_one()
        admin=user(f'task220-admin-{tag}@invalid.example','admin',None); agr=user(f'task220-agr-{tag}@invalid.example','agronomist',first['enterprise_id']); viewer=user(f'task220-view-{tag}@invalid.example','viewer',first['enterprise_id']); cross=user(f'task220-cross-{tag}@invalid.example','agronomist',other['enterprise_id'])
        baseline_id=c.execute(text("INSERT INTO ndvi_records(field_id,captured_date,processed_at,mean_ndvi,min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,satellite) VALUES(:f,:d,now(),.40,.2,.6,.1,.3,.5,5,95,'Sentinel-2') ON CONFLICT(field_id,captured_date) DO UPDATE SET mean_ndvi=.40,cloud_cover_pct=5,valid_pixels_pct=95 RETURNING id"),{'f':first['id'],'d':today-timedelta(days=15)}).scalar_one()
        inspection=c.execute(text("""INSERT INTO field_inspections(field_id,enterprise_id,created_by_id,assigned_to_id,client_request_id,request_fingerprint,source,source_kind,source_reason_codes,title,instructions,status,version,created_at,updated_at,source_reason,priority,submitted_at,source_snapshot_locked)
          VALUES(:field,:enterprise,:admin,:agr,:key,:fp,'manual','manual','[]'::jsonb,'TASK 220 submitted inspection','Qualified finding','submitted',1,now(),now(),'Isolated closed-loop qualification','high',now(),true) RETURNING id"""),{'field':first['id'],'enterprise':first['enterprise_id'],'admin':admin,'agr':agr,'key':f'task220-{tag}','fp':'a'*64}).scalar_one()
        c.execute(text("""INSERT INTO inspection_results(inspection_id,field_id,enterprise_id,recorded_by_id,cause_code,severity,affected_area_ha,observations,recommended_action,sync_state,version,created_at,updated_at)
          VALUES(:i,:f,:e,:u,'nutrient_deficiency','high',1.2,'Field symptoms recorded','Take samples before deciding treatment','server',1,now(),now())"""),{'i':inspection,'f':first['id'],'e':first['enterprise_id'],'u':agr})
    actors={'admin':SimpleNamespace(id=admin,role='admin',enterprise_id=None),'agronomist':SimpleNamespace(id=agr,role='agronomist',enterprise_id=first['enterprise_id']),'viewer':SimpleNamespace(id=viewer,role='viewer',enterprise_id=first['enterprise_id']),'cross':SimpleNamespace(id=cross,role='agronomist',enterprise_id=other['enterprise_id'])}; current={'actor':actors['admin']}
    app.dependency_overrides[get_current_active_user]=lambda: current['actor']; client=TestClient(app); calls=0
    def call(method,url,**kwargs):
        nonlocal calls; calls+=1; return client.request(method,url,**kwargs)
    key=f'draft-{tag}'; payload={'inspection_id':inspection,'reason':'Create deterministic qualified plan'}
    draft=require(call('POST','/api/agronomy-plans',json=payload,headers={'Idempotency-Key':key}),201,'draft'); assert require(call('POST','/api/agronomy-plans',json=payload,headers={'Idempotency-Key':key}),201,'draft replay')==draft
    plan_id=draft['plan_id']; require(call('POST','/api/agronomy-plans',json={**payload,'reason':'Different payload rejected'},headers={'Idempotency-Key':key}),409,'key conflict')
    current['actor']=actors['cross']; require(call('GET',f'/api/agronomy-plans/{plan_id}'),404,'cross tenant'); current['actor']=actors['viewer']; detail=require(call('GET',f'/api/agronomy-plans/{plan_id}'),200,'viewer read'); require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'approve','expected_version':detail['version'],'reason':'Viewer may not approve'},headers={'Idempotency-Key':f'viewer-{tag}'}),403,'viewer write')
    current['actor']=actors['admin']; detail=require(call('GET',f'/api/agronomy-plans/{plan_id}'),200,'detail'); due=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    work_payload={'category':'sampling','instruction':'Collect soil and plant samples and record the result','assigned_to_id':agr,'planned_start_at':None,'due_at':due,'geometry':inside_geometry,'expected_version':detail['version'],'reason':'Evidence-based diagnostic work'}
    require(call('POST',f'/api/agronomy-plans/{plan_id}/work',json={**work_payload,'geometry':{'type':'Point','coordinates':[179.9,89.9]}},headers={'Idempotency-Key':f'outside-{tag}'}),422,'outside field geometry')
    added=require(call('POST',f'/api/agronomy-plans/{plan_id}/work',json=work_payload,headers={'Idempotency-Key':f'work-{tag}'}),201,'work'); item_id=added['item_id']; item_version=added['item_version']
    approve=require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'approve','expected_version':added['version'],'reason':'Agronomist reviewed scope and deadline'},headers={'Idempotency-Key':f'approve-{tag}'}),200,'approve')
    require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'cancel','expected_version':added['version'],'reason':'Stale version must conflict'},headers={'Idempotency-Key':f'stale-{tag}'}),409,'optimistic conflict')
    current['actor']=actors['agronomist']; started=require(call('POST',f'/api/agronomy-plans/{plan_id}/work/{item_id}/transition',json={'operation':'start','expected_plan_version':approve['version'],'expected_version':item_version,'reason':'Worker started assigned sampling','assigned_to_id':agr,'due_at':due,'planned_start_at':None,'result_note':None},headers={'Idempotency-Key':f'start-{tag}'}),200,'start')
    evidence=require(call('POST',f'/api/agronomy-plans/{plan_id}/work/{item_id}/evidence',data={'expected_plan_version':started['version'],'expected_version':started['item_version'],'key':f'evidence-{tag}'},files={'photo':('evidence.png',PNG,'image/png')}),201,'evidence')
    downloaded=call('GET',f"/api/agronomy-plans/{plan_id}/evidence/{evidence['photo_id']}"); assert downloaded.status_code==200 and downloaded.content==PNG
    completed=require(call('POST',f'/api/agronomy-plans/{plan_id}/work/{item_id}/transition',json={'operation':'complete','expected_plan_version':evidence['version'],'expected_version':evidence['item_version'],'reason':'Samples were collected and logged','assigned_to_id':agr,'due_at':due,'planned_start_at':None,'result_note':'Samples collected with photographic evidence'},headers={'Idempotency-Key':f'complete-{tag}'}),200,'complete'); assert completed['status']=='pending_verification'
    require(call('POST',f'/api/agronomy-plans/{plan_id}/reevaluate',json={'expected_version':completed['version'],'reason':'Reject a pre-completion observation explicitly','observation_id':baseline_id},headers={'Idempotency-Key':f'precompletion-{tag}'}),422,'pre-completion scene')
    early=require(call('POST',f'/api/agronomy-plans/{plan_id}/reevaluate',json={'expected_version':completed['version'],'reason':'Explicit early verification check','observation_id':None},headers={'Idempotency-Key':f'early-{tag}'}),200,'too early'); early_detail=require(call('GET',f'/api/agronomy-plans/{plan_id}'),200,'early detail'); assert early_detail['verification_status']=='TOO_EARLY'
    with engine.begin() as c:
        c.execute(text('UPDATE agronomy_plans SET completed_at=:when WHERE id=:id'),{'when':datetime.now(timezone.utc)-timedelta(days=9),'id':plan_id})
        post=c.execute(text("INSERT INTO ndvi_records(field_id,captured_date,processed_at,mean_ndvi,min_ndvi,max_ndvi,std_ndvi,p10_ndvi,p90_ndvi,cloud_cover_pct,valid_pixels_pct,satellite) VALUES(:f,:d,now(),.48,.25,.7,.1,.35,.6,4,96,'Sentinel-2') ON CONFLICT(field_id,captured_date) DO UPDATE SET mean_ndvi=.48,cloud_cover_pct=4,valid_pixels_pct=96 RETURNING id"),{'f':first['id'],'d':today}).scalar_one()
    improved=require(call('POST',f'/api/agronomy-plans/{plan_id}/reevaluate',json={'expected_version':early['version'],'reason':'Evaluate eligible accepted observation','observation_id':post},headers={'Idempotency-Key':f'improved-{tag}'}),200,'improved'); improved_detail=require(call('GET',f'/api/agronomy-plans/{plan_id}'),200,'improved detail'); assert improved_detail['verification_status']=='IMPROVED'
    current['actor']=actors['admin']; closed=require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'close','expected_version':improved['version'],'reason':'Eligible observation improved after completed work'},headers={'Idempotency-Key':f'close-{tag}'}),200,'close'); assert closed['status']=='closed'
    reopened=require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'reopen','expected_version':closed['version'],'reason':'Authorized operator reopened the resolved case'},headers={'Idempotency-Key':f'reopen-{tag}'}),200,'reopen'); assert reopened['status']=='rework'
    reinspection=require(call('POST',f'/api/agronomy-plans/{plan_id}/transition',json={'operation':'reinspection','expected_version':reopened['version'],'reason':'Ineffective outcome requires a documented follow-up inspection'},headers={'Idempotency-Key':f'reinspection-{tag}'}),200,'reinspection'); assert reinspection['follow_up_inspection_id']
    require(call('GET','/api/agronomy-plans/queue'),200,'queue'); require(call('GET','/api/agronomy-plans/summary'),200,'summary'); csv=call('GET','/api/agronomy-plans/export.csv'); assert csv.status_code==200 and str(plan_id) in csv.text
    final=require(call('GET',f'/api/agronomy-plans/{plan_id}'),200,'final'); assert len(final['timeline'])>=9 and len(final['verifications'])==2 and len(final['photos'])==1 and final['status']=='rework'
    with engine.connect() as c:
        duplicates=c.execute(text('SELECT count(*) FROM (SELECT actor_key,command_key,count(*) FROM agronomy_events GROUP BY 1,2 HAVING count(*)>1) x')).scalar_one(); orphan=c.execute(text('SELECT count(*) FROM agronomy_plans p LEFT JOIN fields f ON f.id=p.field_id AND f.enterprise_id=p.enterprise_id WHERE f.id IS NULL')).scalar_one()
    app.dependency_overrides.clear(); result={'status':'PASS','database':engine.url.database,'http_calls':calls,'plan_id':plan_id,'work_item_id':item_id,'evidence_count':1,'source_linkage':['manual'],'geometry_inside_accepted':True,'geometry_outside_rejected':True,'pre_completion_scene_rejected':True,'verification_statuses':['TOO_EARLY','IMPROVED'],'lifecycle':['draft','approved','in_progress','pending_verification','closed','rework'],'follow_up_inspection_id':reinspection['follow_up_inspection_id'],'timeline_events':len(final['timeline']),'optimistic_conflicts':1,'idempotent_replays':1,'cross_tenant_denials':1,'viewer_write_denials':1,'duplicate_command_identities':duplicates,'tenant_orphans':orphan,'production_data_touched':False}
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result)); return 0
if __name__=='__main__': raise SystemExit(main())
