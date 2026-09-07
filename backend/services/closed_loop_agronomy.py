"""One inspection-rooted case chain, explicit SQL, transactional commands."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

import models.registry  # canonical metadata, no relationship loading
from database import Base
from services import anomaly_inspections as inspections
from services import agronomy_policy as policy

DECISION_ROLES = {'admin', 'manager', 'agronomist'}
TERMINAL = {'closed', 'cancelled', 'superseded'}


def table(name):
    return Base.metadata.tables[name]


def now():
    return datetime.now(timezone.utc)


def rows(db, sql, params=None):
    return [dict(r) for r in db.execute(text(sql), params or {}).mappings()]


def one(db, sql, params=None):
    found = rows(db, sql, params)
    return found[0] if found else None


def insert(db, name, values):
    target = table(name)
    return dict(db.execute(target.insert().values(**values).returning(*target.c)).mappings().one())


def change(db, name, identifier, values):
    target = table(name)
    return dict(db.execute(update(target).where(target.c.id == identifier).values(**values).returning(*target.c)).mappings().one())


def actor_key(actor):
    return 'collector' if actor is None else f'user:{actor.user_id}'


def fingerprint(operation, target, payload):
    return inspections._fingerprint({'operation': operation, 'target': target, 'payload': jsonable_encoder(payload)})


def plan_row(db, actor, identifier, lock=False):
    tenant, params = inspections._tenant_clause(actor, 'p')
    result = one(db, 'SELECT p.* FROM agronomy_plans p WHERE p.id=:id' + tenant + (' FOR UPDATE' if lock else ''), {'id':identifier, **params})
    if not result:
        raise HTTPException(404, 'Plan not found')
    return result


def replay(db, actor, key, digest):
    result = one(db, 'SELECT fingerprint,response,event_type FROM agronomy_events WHERE actor_key=:actor AND command_key=:key', {'actor':actor_key(actor), 'key':key})
    if result:
        if result['fingerprint'] != digest:
            raise HTTPException(409, 'Idempotency key payload conflict')
        return result['response']
    return None


def result(plan, **extra):
    return {'plan_id':plan['id'], 'version':plan['version'], 'status':plan['status'], **extra}


def event(db, actor, plan, kind, key, digest, detail, response):
    insert(db, 'agronomy_events', {
        'enterprise_id':plan['enterprise_id'], 'field_id':plan['field_id'], 'inspection_id':plan['inspection_id'],
        'plan_id':plan['id'], 'actor_id':actor.user_id if actor else None, 'actor_key':actor_key(actor),
        'command_key':key, 'fingerprint':digest, 'event_type':kind, 'version':plan['version'],
        'detail':jsonable_encoder(detail), 'response':jsonable_encoder(response),
    })


def check_version(db, actor, plan, expected, key, digest, *, item=None, item_expected=None):
    if plan['version'] == expected and (item is None or item['version'] == item_expected):
        return
    conflict_key = hashlib.sha256(f'{key}:{digest}:conflict'.encode()).hexdigest()
    if not replay(db, actor, conflict_key, digest):
        event(db, actor, plan, 'conflict', conflict_key, digest,
              {'expected_version':expected, 'actual_version':plan['version'], 'item_id':item['id'] if item else None}, result(plan))
        db.commit()
    raise HTTPException(409, 'Version conflict: reload the case and review your retained draft')


def _write(operation):
    """Rollback every failed command; expose no SQL or parameter values."""
    def execute(db, *args, **kwargs):
        try:
            return operation(db, *args, **kwargs)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, 'Concurrent command or case invariant conflict') from None
        except Exception:
            db.rollback()
            raise
    return execute


def observation(db, field_id, *, before=None, after=None, identifier=None, accepted=True):
    params = {'field':field_id}
    where = 'field_id=:field'
    if before:
        where += ' AND captured_date<=:before'; params['before']=policy.day(before)
    if after:
        where += ' AND captured_date>:after'; params['after']=policy.day(after)
    if identifier:
        where += ' AND id=:id'; params['id']=identifier
    if accepted:
        where += ' AND mean_ndvi BETWEEN -1 AND 1 AND valid_pixels_pct BETWEEN 50 AND 100 AND cloud_cover_pct BETWEEN 0 AND 30'
    return one(db, 'SELECT id,captured_date AS date,mean_ndvi AS value,min_ndvi AS min,max_ndvi AS max,p10_ndvi AS p10,p90_ndvi AS p90,cloud_cover_pct AS cloud,valid_pixels_pct AS valid,satellite FROM ndvi_records WHERE '+where+' ORDER BY captured_date DESC,id DESC LIMIT 1', params)


def source_snapshot(db, source):
    finding = one(db, 'SELECT cause_code,cause_details,severity,observations,recommended_action,affected_area_ha,affected_area_pct FROM inspection_results WHERE inspection_id=:id', {'id':source['id']})
    baseline = observation(db, source['field_id'], before=now())
    season = one(db, 'SELECT s.season_year,s.variety,s.planting_date,s.expected_harvest_date,c.name_ru AS crop_name FROM crop_seasons s JOIN crop_types c ON c.id=s.crop_type_id WHERE s.field_id=:field AND s.season_year=:year ORDER BY s.id DESC LIMIT 1', {'field':source['field_id'], 'year':now().year})
    freshness = one(db, "SELECT status,last_accepted_at,last_quality_reason,last_failure_reason FROM satellite_field_freshness WHERE field_id=:field AND enterprise_id=:enterprise AND index_code='ndvi'", {'field':source['field_id'], 'enterprise':source['enterprise_id']})
    indices = rows(db, "SELECT DISTINCT ON (index_code) index_code,captured_date,mean_value,valid_pixels_pct,cloud_cover_pct FROM satellite_index_records WHERE field_id=:field AND captured_date<=CURRENT_DATE AND mean_value IS NOT NULL AND valid_pixels_pct>=50 AND cloud_cover_pct<=30 ORDER BY index_code,captured_date DESC LIMIT 5", {'field':source['field_id']})
    candidate = one(db, 'SELECT id,source_key,zone_key,scene_id,confidence,evidence,explanation FROM autonomous_anomaly_candidates WHERE inspection_id=:id AND field_id=:field AND enterprise_id=:enterprise', {'id':source['id'], 'field':source['field_id'], 'enterprise':source['enterprise_id']})
    return jsonable_encoder({'as_of':now().date(), 'inspection_version':source['version'], 'finding':finding, 'baseline':baseline,
        'season':season, 'freshness':freshness, 'indices':indices, 'candidate':candidate,
        'source':inspections._inspection_item(source)['source']})


def create_plan(db, actor, source, snapshot, *, supersedes=None):
    recommendation = policy.recommend(snapshot)
    candidate = snapshot.get('candidate') or {}
    baseline = snapshot.get('baseline')
    return insert(db, 'agronomy_plans', {
        'enterprise_id':source['enterprise_id'], 'field_id':source['field_id'], 'inspection_id':source['id'],
        'candidate_id':candidate.get('id'), 'alert_id':source['source_alert_id'], 'supersedes_id':supersedes,
        'decision':recommendation['decision'], 'objective':recommendation['objective'], 'expected_outcome':recommendation['expected_outcome'],
        'priority':source['priority'], 'policy_version':policy.POLICY, 'input_snapshot':snapshot, 'recommendation':recommendation,
        'status':'draft', 'created_by_id':actor.user_id, 'baseline_record_id':baseline['id'] if baseline else None, 'baseline':baseline,
    })


@_write
def draft(db, user, payload, key):
    actor = inspections._actor(user, write=True)
    inspections._write_roles(actor, DECISION_ROLES)
    source = inspections._inspection_row(db, actor, payload.inspection_id)
    db.execute(text('SELECT id FROM field_inspections WHERE id=:id FOR UPDATE'), {'id':source['id']})
    digest = fingerprint('draft', source['id'], payload)
    prior = replay(db, actor, key, digest)
    if prior: return prior
    if source['status'] not in {'submitted', 'confirmed'}:
        raise HTTPException(409, 'A submitted inspection is required')
    if one(db, "SELECT id FROM agronomy_plans WHERE inspection_id=:id AND status NOT IN ('closed','cancelled','superseded')", {'id':source['id']}):
        raise HTTPException(409, 'This inspection already has an active plan')
    snapshot = source_snapshot(db, source)
    plan = create_plan(db, actor, source, snapshot)
    response = result(plan)
    event(db, actor, plan, 'recommendation_created', key, digest, {'reason':payload.reason, 'policy_version':policy.POLICY}, response)
    db.commit()
    return response


def begin_command(db, user, identifier, operation, payload, key):
    actor = inspections._actor(user, write=True)
    inspections._write_roles(actor, DECISION_ROLES)
    plan = plan_row(db, actor, identifier, lock=True)
    digest = fingerprint(operation, identifier, payload)
    prior = replay(db, actor, key, digest)
    return actor, plan, digest, prior


@_write
def edit(db, user, identifier, payload, key):
    actor, plan, digest, prior = begin_command(db, user, identifier, 'edit', payload, key)
    if prior: return prior
    check_version(db, actor, plan, payload.expected_version, key, digest)
    if plan['status'] not in {'draft','rework'}:
        raise HTTPException(409, 'Only a draft or rework decision can be edited')
    values = payload.model_dump(exclude={'expected_version','reason'})
    previous = {k:plan[k] for k in values}
    plan = change(db, 'agronomy_plans', identifier, {**values, 'version':plan['version']+1})
    response = result(plan)
    event(db, actor, plan, 'decision_edited', key, digest, {'reason':payload.reason, 'before':previous, 'after':values}, response)
    db.commit(); return response


def validate_assignment(db, plan, assignee):
    if assignee is not None:
        inspections._eligible_user(db, plan['enterprise_id'], assignee)


def validate_geometry(db, plan, geometry):
    if geometry is None: return None
    from shapely.geometry import shape
    from geoalchemy2.shape import from_shape
    try:
        value = shape(geometry)
        if value.is_empty or not value.is_valid or value.has_z or len(value.bounds)!=4:
            raise ValueError()
        xmin,ymin,xmax,ymax = value.bounds
        if not (-180<=xmin<=xmax<=180 and -90<=ymin<=ymax<=90): raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, 'Invalid EPSG:4326 work geometry') from None
    covered = db.execute(text('SELECT ST_Covers(geometry,ST_SetSRID(ST_GeomFromGeoJSON(:geometry),4326)) FROM fields WHERE id=:field AND enterprise_id=:enterprise'), {'geometry':json.dumps(geometry, allow_nan=False), 'field':plan['field_id'], 'enterprise':plan['enterprise_id']}).scalar()
    if not covered: raise HTTPException(422, 'Work geometry is outside the authorized field')
    return from_shape(value, srid=4326)


@_write
def add_work(db, user, identifier, payload, key):
    actor, plan, digest, prior = begin_command(db, user, identifier, 'add_work', payload, key)
    if prior: return prior
    check_version(db, actor, plan, payload.expected_version, key, digest)
    if plan['status'] not in {'draft','rework'}: raise HTTPException(409, 'Work is edited before approval')
    count = db.execute(text('SELECT count(*) FROM agronomy_work_items WHERE plan_id=:id AND cycle=:cycle'), {'id':identifier, 'cycle':plan['cycle']}).scalar_one()
    if count>=20: raise HTTPException(422, 'At most 20 work items per cycle')
    validate_assignment(db, plan, payload.assigned_to_id)
    item = insert(db, 'agronomy_work_items', {
        'plan_id':identifier, 'inspection_id':plan['inspection_id'], 'enterprise_id':plan['enterprise_id'], 'field_id':plan['field_id'],
        'cycle':plan['cycle'], **payload.model_dump(exclude={'expected_version','reason','geometry'}), 'geometry':validate_geometry(db, plan, payload.geometry),
    })
    plan = change(db, 'agronomy_plans', identifier, {'version':plan['version']+1})
    response = result(plan, item_id=item['id'], item_version=item['version'])
    event(db, actor, plan, 'work_created', key, digest, {'reason':payload.reason, 'item':payload.model_dump(mode='json')}, response)
    db.commit(); return response


def current_work(db, plan):
    return rows(db, 'SELECT id,status,assigned_to_id,due_at,planned_start_at FROM agronomy_work_items WHERE plan_id=:id AND cycle=:cycle ORDER BY id', {'id':plan['id'], 'cycle':plan['cycle']})


def reconcile_candidate(db, actor, plan, target, reason):
    if not plan['candidate_id']: return
    candidate = one(db, 'SELECT id,state,version FROM autonomous_anomaly_candidates WHERE id=:id AND inspection_id=:inspection AND field_id=:field AND enterprise_id=:enterprise FOR UPDATE', {'id':plan['candidate_id'], 'inspection':plan['inspection_id'], 'field':plan['field_id'], 'enterprise':plan['enterprise_id']})
    if not candidate: raise HTTPException(409, 'Source candidate binding changed')
    if candidate['state']==target: return
    db.execute(text('UPDATE autonomous_anomaly_candidates SET state=:target,version=version+1,updated_at=now(),resolved_at=:resolved WHERE id=:id'), {'id':candidate['id'], 'target':target, 'resolved':now() if target=='RESOLVED' else None})
    insert(db, 'autonomous_anomaly_transitions', {'candidate_id':candidate['id'], 'enterprise_id':plan['enterprise_id'], 'from_state':candidate['state'], 'to_state':target, 'actor_id':actor.user_id if actor else None, 'reason':reason, 'expected_version':candidate['version']})


def follow_up(db, actor, plan, reason, key):
    """Create within this transaction using the accepted inspection schema/history."""
    request_key = 'f-reinspect-' + hashlib.sha256(key.encode()).hexdigest()[:40]
    inspection = insert(db, 'field_inspections', {
        'enterprise_id':plan['enterprise_id'], 'field_id':plan['field_id'], 'created_by_id':actor.user_id,
        'updated_by_id':actor.user_id, 'client_request_id':request_key, 'request_fingerprint':fingerprint('reinspection',plan['id'],reason),
        'source':'manual', 'source_kind':'manual', 'source_priority':plan['priority'], 'source_reason_codes':[],
        'title':'Повторный осмотр по плану '+str(plan['id']), 'instructions':reason, 'source_reason':reason,
        'priority':plan['priority'], 'status':'new', 'follow_up_of_id':plan['inspection_id'],
    })
    inspections._audit(db, actor, inspection, 'inspection_created', inspection['version'], {'plan_id':plan['id'], 'reason':reason}, key=request_key)
    return inspection['id']


@_write
def transition(db, user, identifier, payload, key):
    actor, plan, digest, prior = begin_command(db, user, identifier, 'transition', payload, key)
    if prior: return prior
    check_version(db, actor, plan, payload.expected_version, key, digest)
    operation = payload.operation
    status = plan['status']; values = {'version':plan['version']+1}; extra = {}
    if operation=='approve':
        if status not in {'draft','rework'}: raise HTTPException(409, 'Plan cannot be approved in this state')
        work = [i for i in current_work(db,plan) if i['status']!='cancelled']
        if not work or any(i['status']!='planned' or not i['assigned_to_id'] or not i['due_at'] for i in work):
            raise HTTPException(409, 'Approval requires assigned work with deadlines')
        for item in work: validate_assignment(db,plan,item['assigned_to_id'])
        baseline = observation(db, plan['field_id'], before=now())
        values.update(status='approved', approved_at=now(), approved_by_id=actor.user_id,
                      baseline_record_id=baseline['id'] if baseline else None, baseline=jsonable_encoder(baseline))
    elif operation=='cancel':
        if status in TERMINAL or status=='pending_verification': raise HTTPException(409, 'Use a reasoned resolution or rework after completion')
        db.execute(text("UPDATE agronomy_work_items SET status='cancelled',version=version+1 WHERE plan_id=:id AND status IN ('planned','in_progress')"), {'id':identifier})
        values.update(status='cancelled', resolution_reason=payload.reason)
    elif operation in {'close','override_close'}:
        if status!='pending_verification': raise HTTPException(409, 'Complete every required work item before resolution')
        if operation=='close' and plan['verification_status']!='IMPROVED': raise HTTPException(409, 'An improved eligible observation is required; use an explicit override with reason')
        reconcile_candidate(db,actor,plan,'RESOLVED',payload.reason)
        values.update(status='closed', closed_at=now(), closed_by_id=actor.user_id, resolution_reason=payload.reason)
    elif operation in {'rework','reinspection','reopen'}:
        allowed = {'closed'} if operation=='reopen' else {'pending_verification','rework'}
        if status not in allowed: raise HTTPException(409, 'This decision is not available in the current state')
        if status!='rework':
            values.update(status='rework', cycle=plan['cycle']+1, completed_at=None, completed_by_id=None,
                          started_at=None, started_by_id=None, closed_at=None, closed_by_id=None, verification_status='PENDING_DATA')
        values['resolution_reason']=payload.reason
        if operation=='reopen': reconcile_candidate(db,actor,plan,'INSPECTION_CREATED',payload.reason)
        if operation=='reinspection': extra['follow_up_inspection_id']=follow_up(db,actor,plan,payload.reason,key)
    elif operation=='supersede':
        if status not in {'draft','approved','rework'}: raise HTTPException(409, 'Started work must be completed or explicitly cancelled first')
        values.update(status='superseded', resolution_reason=payload.reason)
        plan = change(db, 'agronomy_plans',identifier,values)
        source = inspections._inspection_row(db,actor,plan['inspection_id'])
        replacement = create_plan(db,actor,source,source_snapshot(db,source),supersedes=identifier)
        extra['superseding_plan_id']=replacement['id']
        event(db,actor,replacement,'recommendation_created',hashlib.sha256((key+':replacement').encode()).hexdigest(),digest,{'reason':payload.reason,'supersedes':identifier},result(replacement))
    else: raise HTTPException(422, 'Unsupported transition')
    if operation!='supersede': plan=change(db,'agronomy_plans',identifier,values)
    response=result(plan,**extra)
    event(db,actor,plan,operation,key,digest,{'reason':payload.reason,'from_status':status,'to_status':plan['status'],'baseline':plan['baseline'] if operation=='approve' else None},response)
    db.commit(); return response


def work_row(db, plan, item_id):
    item=one(db, 'SELECT w.*,ST_AsGeoJSON(w.geometry)::json AS work_geometry FROM agronomy_work_items w WHERE id=:id AND plan_id=:plan FOR UPDATE', {'id':item_id,'plan':plan['id']})
    if not item: raise HTTPException(404,'Work item not found')
    return item


def execution_permission(actor,item):
    if actor.role=='agronomist' and item['assigned_to_id']!=actor.user_id:
        raise HTTPException(403,'Only the assigned worker can execute this work')


@_write
def work_transition(db,user,identifier,item_id,payload,key):
    actor,plan,digest,prior=begin_command(db,user,identifier,'work:'+str(item_id),payload,key)
    if prior: return prior
    item=work_row(db,plan,item_id)
    check_version(db,actor,plan,payload.expected_plan_version,key,digest,item=item,item_expected=payload.expected_version)
    if item['cycle']!=plan['cycle']: raise HTTPException(409,'This work belongs to an earlier cycle')
    operation=payload.operation; item_values={'version':item['version']+1}; plan_values={'version':plan['version']+1}
    if operation=='assign':
        if plan['status'] not in {'draft','approved','rework'} or item['status']!='planned': raise HTTPException(409,'Assignment is allowed before execution')
        if not payload.assigned_to_id or not payload.due_at: raise HTTPException(422,'Assignee and deadline are required')
        if payload.planned_start_at and payload.due_at<payload.planned_start_at: raise HTTPException(422,'Deadline precedes start')
        validate_assignment(db,plan,payload.assigned_to_id)
        item_values.update(assigned_to_id=payload.assigned_to_id,due_at=payload.due_at,planned_start_at=payload.planned_start_at)
    elif operation=='start':
        execution_permission(actor,item)
        if plan['status'] not in {'approved','in_progress'} or item['status']!='planned' or not item['assigned_to_id'] or not item['due_at']: raise HTTPException(409,'Approved, assigned work is required')
        item_values.update(status='in_progress',started_at=now())
        plan_values.update(status='in_progress',started_at=plan['started_at'] or now(),started_by_id=plan['started_by_id'] or actor.user_id)
    elif operation=='complete':
        execution_permission(actor,item)
        if plan['status']!='in_progress' or item['status']!='in_progress': raise HTTPException(409,'Start work before completion')
        if not payload.result_note or len(payload.result_note.strip())<5: raise HTTPException(422,'Execution evidence note is required')
        item_values.update(status='completed',completed_at=now(),result_note=payload.result_note)
    elif operation=='cancel':
        if plan['status'] not in {'draft','rework'} or item['status']!='planned': raise HTTPException(409,'Approved required work cannot be silently removed')
        item_values.update(status='cancelled',result_note=payload.reason)
    item=change(db,'agronomy_work_items',item_id,item_values)
    work=[i for i in current_work(db,plan) if i['status']!='cancelled']
    if operation=='complete' and work and all(i['status']=='completed' for i in work):
        plan_values.update(status='pending_verification',completed_at=now(),completed_by_id=actor.user_id,verification_status='PENDING_DATA')
    plan=change(db,'agronomy_plans',identifier,plan_values)
    response=result(plan,item_id=item_id,item_version=item['version'])
    event(db,actor,plan,'work_'+operation,key,digest,{'item_id':item_id,'reason':payload.reason,'result_note':payload.result_note,'assigned_to_id':item['assigned_to_id'],'due_at':item['due_at']},response)
    db.commit(); return response


def evaluate_plan(db,actor,plan,key,digest,*,observation_id=None,reason='Scheduled accepted-observation reconciliation'):
    if plan['status']!='pending_verification' or not plan['completed_at']: raise HTTPException(409,'A completed plan is required')
    first_after=policy.day(plan['completed_at'])+timedelta(days=policy.WAIT_DAYS)
    post=observation(db,plan['field_id'],after=first_after,identifier=observation_id,accepted=True)
    if observation_id and not post: raise HTTPException(422,'Observation is not an eligible accepted post-completion scene')
    freshness=one(db,"SELECT status FROM satellite_field_freshness WHERE enterprise_id=:enterprise AND field_id=:field AND index_code='ndvi'",{'enterprise':plan['enterprise_id'],'field':plan['field_id']})
    state=(freshness or {}).get('status')
    if not post:
        rejected=observation(db,plan['field_id'],after=first_after,accepted=False)
        if rejected: state=policy.quality(rejected) or state
    source=plan['input_snapshot'].get('source') or {}
    zone_required=bool(source.get('zone') or source.get('point') or plan['candidate_id'])
    measurement=policy.evaluate(plan['baseline'],post,plan['completed_at'],now(),freshness=state,zone_required=zone_required)
    evaluation_key=hashlib.sha256(json.dumps({'post':post['id'] if post else None,'status':measurement['status'],'baseline':plan['baseline_record_id']},sort_keys=True).encode()).hexdigest()
    existing=one(db,'SELECT id,status FROM agronomy_verifications WHERE plan_id=:id AND cycle=:cycle AND evaluation_key=:key AND policy_version=:policy',{'id':plan['id'],'cycle':plan['cycle'],'key':evaluation_key,'policy':policy.POLICY})
    if existing:
        response=result(plan,verification_id=existing['id'])
        if actor: event(db,actor,plan,'verification_reused',key,digest,{'reason':reason,'verification_id':existing['id']},response)
        return response,measurement['status']
    verification=insert(db,'agronomy_verifications',{
        'plan_id':plan['id'],'inspection_id':plan['inspection_id'],'enterprise_id':plan['enterprise_id'],'field_id':plan['field_id'],
        'cycle':plan['cycle'],'baseline_record_id':plan['baseline_record_id'],'post_record_id':post['id'] if post else None,
        'evaluation_key':evaluation_key,'policy_version':policy.POLICY,'status':measurement['status'],'measurements':jsonable_encoder(measurement),
        'completed_at':plan['completed_at'],'post_date':post['date'] if post else None,
    })
    plan=change(db,'agronomy_plans',plan['id'],{'verification_status':measurement['status'],'version':plan['version']+1})
    response=result(plan,verification_id=verification['id'])
    event(db,actor,plan,'verification',key,digest,{'reason':reason,'verification_id':verification['id'],'status':measurement['status']},response)
    return response,measurement['status']


@_write
def reevaluate(db,user,identifier,payload,key):
    actor,plan,digest,prior=begin_command(db,user,identifier,'reevaluate',payload,key)
    if prior: return prior
    check_version(db,actor,plan,payload.expected_version,key,digest)
    response,_=evaluate_plan(db,actor,plan,key,digest,observation_id=payload.observation_id,reason=payload.reason)
    db.commit(); return response


def queue_filter(actor, filters):
    tenant,params=inspections._tenant_clause(actor,'p'); where='TRUE'+tenant
    for key in ('enterprise_id','field_id','priority','status','verification_status','inspection_id'):
        value=filters.get(key)
        if value is not None:
            where+=f' AND p.{key}=:{key}'; params[key]=value
    if filters.get('source_kind'):
        where+=' AND i.source_kind=:source_kind'; params['source_kind']=filters['source_kind']
    if filters.get('assigned_to_id'):
        where+=' AND EXISTS (SELECT 1 FROM agronomy_work_items w WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.assigned_to_id=:assigned_to_id)'; params['assigned_to_id']=filters['assigned_to_id']
    if filters.get('due_state')=='overdue':
        where+=" AND p.status NOT IN ('closed','cancelled','superseded') AND EXISTS (SELECT 1 FROM agronomy_work_items w WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.status IN ('planned','in_progress') AND w.due_at<now())"
    elif filters.get('due_state')=='due':
        where+=" AND p.status NOT IN ('closed','cancelled','superseded') AND EXISTS (SELECT 1 FROM agronomy_work_items w WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.status IN ('planned','in_progress') AND w.due_at>=now())"
    return where,params


QUEUE_FROM=' FROM agronomy_plans p JOIN field_inspections i ON i.id=p.inspection_id JOIN fields f ON f.id=p.field_id JOIN enterprises e ON e.id=p.enterprise_id WHERE '


def list_queue(db,user,filters):
    actor=inspections._actor(user); where,params=queue_filter(actor,filters)
    limit=min(int(filters.get('limit',50)),200); offset=min(int(filters.get('offset',0)),10000)
    total=db.execute(text('SELECT count(*)'+QUEUE_FROM+where),params).scalar_one()
    items=rows(db,"SELECT p.id,p.inspection_id,p.enterprise_id,p.field_id,p.priority,p.status,p.version,p.verification_status,p.created_at,p.decision,f.name AS field_name,e.name AS enterprise_name,i.source_kind,(SELECT min(w.due_at) FROM agronomy_work_items w WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.status IN ('planned','in_progress')) AS due_at"+QUEUE_FROM+where+" ORDER BY CASE p.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,p.created_at DESC,p.id DESC LIMIT :limit OFFSET :offset",{**params,'limit':limit,'offset':offset})
    return {'items':items,'total':total,'limit':limit,'offset':offset}


def summary(db,user,filters):
    actor=inspections._actor(user); where,params=queue_filter(actor,filters)
    return one(db,"""SELECT count(*) FILTER (WHERE p.status NOT IN ('closed','cancelled','superseded')) AS open,
        count(*) FILTER (WHERE p.status NOT IN ('closed','cancelled','superseded') AND EXISTS (SELECT 1 FROM agronomy_work_items w WHERE w.plan_id=p.id AND w.cycle=p.cycle AND w.status IN ('planned','in_progress') AND w.due_at<now())) AS overdue,
        count(*) FILTER (WHERE p.status='pending_verification') AS pending_verification,
        count(*) FILTER (WHERE p.verification_status='IMPROVED') AS improved,
        count(*) FILTER (WHERE p.status='rework' OR p.verification_status IN ('NO_MATERIAL_CHANGE','WORSENED')) AS ineffective,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (p.closed_at-p.created_at))/86400) FILTER (WHERE p.status='closed') AS median_cycle_days"""+QUEUE_FROM+where,params)


def detail(db,user,identifier):
    actor=inspections._actor(user); plan=plan_row(db,actor,identifier)
    source=inspections._inspection_row(db,actor,plan['inspection_id'])
    work=rows(db,'SELECT w.*,ST_AsGeoJSON(w.geometry)::json AS work_geometry,u.full_name AS assignee_name FROM agronomy_work_items w LEFT JOIN users u ON u.id=w.assigned_to_id WHERE w.plan_id=:id ORDER BY w.cycle,w.id LIMIT 200',{'id':identifier})
    for item in work: item.pop('geometry',None)
    photos=rows(db,"SELECT ev.id,ev.agronomy_work_item_id,ev.original_filename,ev.media_type,ev.byte_size,ev.sha256,ev.created_at,ev.version FROM inspection_evidence ev JOIN agronomy_work_items w ON w.id=ev.agronomy_work_item_id WHERE w.plan_id=:id AND ev.deleted_at IS NULL ORDER BY ev.id LIMIT 200",{'id':identifier})
    timeline=rows(db,'SELECT ev.id,ev.event_type,ev.detail,ev.version,ev.occurred_at,ev.actor_id,u.full_name AS actor_name FROM agronomy_events ev LEFT JOIN users u ON u.id=ev.actor_id WHERE ev.plan_id=:id ORDER BY ev.id DESC LIMIT 200',{'id':identifier})
    verifications=rows(db,'SELECT id,cycle,status,measurements,created_at FROM agronomy_verifications WHERE plan_id=:id ORDER BY id DESC LIMIT 100',{'id':identifier})
    assignees=rows(db,"SELECT id,full_name,role FROM users WHERE enterprise_id=:enterprise AND is_active=true AND role IN ('manager','agronomist') ORDER BY full_name,id LIMIT 200",{'enterprise':plan['enterprise_id']})
    field=inspections._field(db,actor,plan['field_id'])
    inspection_events=rows(db,'SELECT ev.event_type,ev.event_metadata,ev.occurred_at,ev.actor_id,u.full_name AS actor_name FROM operational_audit_events ev JOIN users u ON u.id=ev.actor_id WHERE ev.inspection_id=:id ORDER BY ev.id DESC LIMIT 100',{'id':plan['inspection_id']})
    return {**plan,'field_name':field['name'],'enterprise_name':source['enterprise_name'],'field_geometry':field['geometry'],
            'inspection':inspections._inspection_item(source),'finding':plan['input_snapshot'].get('finding'),
            'work_items':work,'photos':photos,'timeline':timeline,'inspection_timeline':inspection_events,'verifications':verifications,'assignees':assignees}


@_write
def upload_evidence(db,user,identifier,item_id,expected_plan_version,expected_version,key,filename,content_type,data):
    actor=inspections._actor(user,write=True); plan=plan_row(db,actor,identifier,lock=True); item=work_row(db,plan,item_id)
    execution_permission(actor,item)
    if not data or len(data)>inspections.MAX_PHOTO_BYTES: raise HTTPException(413,'Photo exceeds the 8 MiB limit')
    media_type,extension=inspections._photo_content_type(data,content_type)
    if not filename or Path(filename).name!=filename or '\\' in filename or '\x00' in filename or len(filename)>255: raise HTTPException(422,'Invalid photo filename')
    digest=fingerprint('evidence:'+str(item_id),identifier,{'version':expected_version,'plan_version':expected_plan_version,'sha256':hashlib.sha256(data).hexdigest(),'filename':filename,'media_type':media_type})
    prior=replay(db,actor,key,digest)
    if prior: return prior
    check_version(db,actor,plan,expected_plan_version,key,digest,item=item,item_expected=expected_version)
    if plan['status']!='in_progress' or item['status']!='in_progress': raise HTTPException(409,'Evidence is recorded during execution')
    totals=one(db,'SELECT count(*) AS count,coalesce(sum(byte_size),0) AS bytes FROM inspection_evidence WHERE agronomy_work_item_id=:id AND deleted_at IS NULL',{'id':item_id})
    if totals['count']>=inspections.MAX_PHOTOS or totals['bytes']+len(data)>inspections.MAX_TOTAL_PHOTO_BYTES: raise HTTPException(413,'Work evidence limit exceeded')
    root=inspections._media_root(); token=str(uuid.uuid4()); storage_key=f'{token[:2]}/{token}{extension}'; target=(root/storage_key).resolve()
    if root not in target.parents: raise HTTPException(500,'Private media path rejected')
    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
    try:
        photo=insert(db,'inspection_evidence',{'inspection_id':plan['inspection_id'],'field_id':plan['field_id'],'enterprise_id':plan['enterprise_id'],'created_by_id':actor.user_id,
            'evidence_type':'photo','provider':'private_runtime','original_filename':filename,'media_type':media_type,'byte_size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'provider_metadata':{},'storage_key':storage_key,'agronomy_work_item_id':item_id})
        item=change(db,'agronomy_work_items',item_id,{'version':item['version']+1})
        plan=change(db,'agronomy_plans',identifier,{'version':plan['version']+1})
        response=result(plan,item_id=item_id,item_version=item['version'],photo_id=photo['id'])
        event(db,actor,plan,'execution_evidence',key,digest,{'item_id':item_id,'photo_id':photo['id'],'sha256':photo['sha256']},response)
        db.commit(); return response
    except Exception:
        target.unlink(missing_ok=True); raise


def photo_file(db,user,identifier,photo_id):
    actor=inspections._actor(user); plan=plan_row(db,actor,identifier)
    photo=one(db,'SELECT ev.id,ev.storage_key,ev.original_filename,ev.media_type,ev.agronomy_work_item_id FROM inspection_evidence ev JOIN agronomy_work_items w ON w.id=ev.agronomy_work_item_id WHERE ev.id=:id AND w.plan_id=:plan AND ev.enterprise_id=:enterprise AND ev.deleted_at IS NULL',{'id':photo_id,'plan':identifier,'enterprise':plan['enterprise_id']})
    if not photo or not inspections.STORAGE_KEY.fullmatch(photo['storage_key'] or ''): raise HTTPException(404,'Photo not found')
    root=inspections._media_root(); path=(root/photo['storage_key']).resolve()
    if root not in path.parents or not path.is_file(): raise HTTPException(404,'Photo not found')
    return photo,path


@_write
def delete_evidence(db,user,identifier,photo_id,payload,key):
    actor,plan,digest,prior=begin_command(db,user,identifier,'delete_evidence:'+str(photo_id),payload,key)
    if prior: return prior
    check_version(db,actor,plan,payload.expected_version,key,digest)
    photo,path=photo_file(db,user,identifier,photo_id); item=work_row(db,plan,photo['agronomy_work_item_id']); execution_permission(actor,item)
    if item['status']!='in_progress': raise HTTPException(409,'Completed execution evidence is immutable')
    change(db,'inspection_evidence',photo_id,{'deleted_at':now(),'deleted_by_id':actor.user_id,'version':table('inspection_evidence').c.version+1})
    plan=change(db,'agronomy_plans',identifier,{'version':plan['version']+1}); response=result(plan,photo_id=photo_id)
    event(db,actor,plan,'evidence_deleted',key,digest,{'photo_id':photo_id,'reason':payload.reason},response)
    db.commit(); path.unlink(missing_ok=True); return response


def reconcile_pending(session_factory, *, limit=100):
    """Called only by the existing collector. Per-case transactions and skip-locked."""
    counters={key:0 for key in ('eligible','improved','unchanged','worsened','pending_quality_provider','conflicts','failures','reopened')}
    with session_factory() as lookup:
        identifiers=list(lookup.execute(text("SELECT id FROM agronomy_plans WHERE status='pending_verification' ORDER BY completed_at,id LIMIT :limit"),{'limit':min(max(limit,1),100)}).scalars())
    for identifier in identifiers:
        with session_factory() as db:
            try:
                plan=one(db,"SELECT * FROM agronomy_plans WHERE id=:id AND status='pending_verification' FOR UPDATE SKIP LOCKED",{'id':identifier})
                if not plan: counters['conflicts']+=1; continue
                counters['eligible']+=1
                key='verify-'+hashlib.sha256(f"{identifier}:{plan['cycle']}:{plan['version']}:{now().date()}".encode()).hexdigest()[:56]
                _,status=evaluate_plan(db,None,plan,key,fingerprint('scheduled_verification',identifier,key))
                db.commit()
                counters[{'IMPROVED':'improved','NO_MATERIAL_CHANGE':'unchanged','WORSENED':'worsened'}.get(status,'pending_quality_provider')]+=1
            except IntegrityError:
                db.rollback(); counters['conflicts']+=1
            except Exception:
                db.rollback(); counters['failures']+=1
    return counters
