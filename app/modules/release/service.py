import json
from datetime import datetime, timezone
from hashlib import sha256
from app.store import connection
from app.modules.runs.repository import lookup

HELD = '隔离'
RELEASED = '放行'


def basis(record):
    if any('跨日' in item['metric'] for item in record.get('proof',[])):
        return '跨日记录'
    return '常规锚段'


def conclude(run_id, run):
    """Turn a stored run into a per-segment release conclusion.

    Only segments already accepted by the timing rule are released; a segment
    whose rewind could not be proven stays quarantined on its own, and every
    other segment is decided independently instead of being held with it.
    """
    unit = run['accepted'][0]['unit'] if run['accepted'] else None
    segments = [{'key':row['key'],'line':row['line'],'revision':row.get('revision'),
                 'outcome':RELEASED,'basis':basis(row),'amount':row['amount'],'unit':row['unit'],
                 'proof':row.get('proof',[])}
                for row in run['accepted']]
    invalid = []
    for row in run['quarantine']:
        if not row.get('key'):
            invalid.append({'line':row['line'],'code':row['code'],'detail':row['detail']})
            continue
        segments.append({'key':row['key'],'line':row['line'],'revision':row.get('revision'),
                         'outcome':HELD,'basis':'无法证明的回退' if row['code']=='SOUND_REWIND' else '来源不合格',
                         'amount':None,'unit':unit,'code':row['code'],'detail':row['detail']})
    segments.sort(key=lambda row:(row['key'],row['line']))
    held = [row for row in segments if row['outcome']==HELD]
    released = [row for row in segments if row['outcome']==RELEASED]
    return {'run_id':run_id,'rule_version':run['manifest']['version'],
            'summary':{'segments':len(segments),'released':len(released),'held':len(held),
                       'invalid':len(invalid)},
            'segments':segments,'invalid':invalid}


def digest(conclusion):
    body = json.dumps(conclusion,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return sha256(body.encode()).hexdigest()


def current(db, run_id):
    row = db.execute('SELECT version,digest,payload,created FROM releases WHERE run_id=? ORDER BY version DESC LIMIT 1',
                     (run_id,)).fetchone()
    if row is None:
        return None
    record = json.loads(row['payload'])
    record.update(version=row['version'],digest=row['digest'],created=row['created'])
    return record


def write(db, run_id, version, stamp, payload, created):
    db.execute('INSERT INTO releases VALUES (?,?,?,?,?)',(run_id,version,stamp,payload,created))


def ensure(run_id, run):
    """Persist the conclusion for ``run_id`` without ever duplicating it.

    The same run (same input bytes and rule version) yields the same digest,
    so a re-run returns the published version unchanged.  Writing happens in a
    single transaction: if it fails, nothing is left behind and the previous
    version stays current.
    """
    conclusion = conclude(run_id,run)
    stamp = digest(conclusion)
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        latest = db.execute('SELECT MAX(version) FROM releases WHERE run_id=?',(run_id,)).fetchone()[0]
        if latest is not None:
            row = db.execute('SELECT digest,payload,created FROM releases WHERE run_id=? AND version=?',
                             (run_id,latest)).fetchone()
            if row['digest'] == stamp:
                record = json.loads(row['payload'])
                record.update(version=latest,digest=stamp,created=row['created'])
                return record
        version = (latest or 0) + 1
        created = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(conclusion,ensure_ascii=False,sort_keys=True)
        write(db,run_id,version,stamp,payload,created)
        record = json.loads(payload)
        record.update(version=version,digest=stamp,created=created)
        return record


def fetch(run_id):
    """Return the published conclusion, forming it for a batch that has none."""
    with connection() as db:
        lookup(db,run_id)
        record = current(db,run_id)
    if record is not None:
        return record
    return publish(run_id)


def publish(run_id):
    """Generate (or keep) the conclusion for a batch.

    Generation is separate from batch execution so a write failure cannot make
    a valid batch unreadable; the caller sees the error while the previous
    conclusion remains current because the write never committed.
    """
    from app.modules.runs.service import get
    return ensure(run_id,get(run_id))
