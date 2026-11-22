import csv
import io
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.modules.release import service
from app.store import connection


HEADER = ['声轨片段','修订号','提交时间','采样率赫兹','允许漂移ppm','锚点','片段始样本','片段末样本','跨日记录','原始备注']
BOOK = '声轨片段,外部记录值(毫秒)\n'


def _csv(rows):
    stream = io.StringIO(newline='')
    csv.writer(stream).writerows([HEADER,*rows])
    return stream.getvalue().encode()


def _night(key, anchors='0:86399000;4000:0;8000:998;12000:1996', cross='4000'):
    return [key,'1','2026-11-18T23:30:00+08:00','4000','2000',anchors,'3000','9000',cross,'']


def _plain(key):
    return [key,'1','2026-11-18T23:30:00+08:00','4000','2000','0:0;4000:1000;8000:1998;12000:2996','3000','9000','','']


def _published(client, rows):
    source = _csv(rows)
    run_id = client.post('/api/runs', json={'data': source.decode(), 'book': BOOK}).json()['manifest']['run_id']
    return run_id, client.get(f'/api/release/{run_id}').json()


def test_only_unproven_segment_is_held(client):
    _, release = _published(client, [_plain('00101'), _night('00102'), _night('00103', cross='')])
    by_key = {row['key']: row for row in release['segments']}
    assert by_key['00101']['outcome'] == '放行'
    assert by_key['00102']['outcome'] == '放行' and by_key['00102']['basis'] == '跨日记录'
    assert by_key['00103']['outcome'] == '隔离' and by_key['00103']['code'] == 'SOUND_REWIND'
    assert release['summary'] == {'segments': 3, 'released': 2, 'held': 1, 'invalid': 0}


def test_release_is_exported(client):
    run_id, release = _published(client, [_plain('00101')])
    proof = client.get(f'/api/export/{run_id}').json()
    assert proof['release'] == release
    assert proof['release']['summary']['released'] == 1


def test_release_unknown_before_run(client):
    response = client.get('/api/release/not-found')
    assert (response.status_code, response.json()['code']) == (404, 'RUN_UNKNOWN')


def test_run_publishes_conclusion(client):
    source = _csv([_plain('00101')])
    run_id = client.post('/api/runs', json={'data': source.decode(), 'book': BOOK}).json()['manifest']['run_id']
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM releases WHERE run_id=?', (run_id,)).fetchone()[0] == 1
    release = client.get(f'/api/release/{run_id}').json()
    assert release['version'] == 1 and release['summary']['released'] == 1


def test_rerun_does_not_add_conclusion(client):
    run_id, first = _published(client, [_plain('00101'), _night('00102')])
    assert client.post(f'/api/release/{run_id}').json()['version'] == first['version']
    assert client.post(f'/api/runs/{run_id}/replay').status_code == 200
    assert client.get(f'/api/release/{run_id}').json() == first
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM releases WHERE run_id=?', (run_id,)).fetchone()[0] == 1


def test_concurrent_publish_one_version(client):
    run_id, _ = _published(client, [_plain('00101'), _night('00102')])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.publish(run_id), range(4)))
    assert {item['version'] for item in results} == {1}
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM releases WHERE run_id=?', (run_id,)).fetchone()[0] == 1


def test_failed_write_keeps_old_conclusion(client, monkeypatch):
    run_id, before = _published(client, [_plain('00101')])

    def broken(db, run_id, version, stamp, payload, created):
        raise RuntimeError('disk full')

    monkeypatch.setattr(service, 'conclude', lambda run_id, run: {
        **before, 'segments': [], 'summary': {'segments': 0, 'released': 0, 'held': 0, 'invalid': 0}})
    monkeypatch.setattr(service, 'write', broken)
    with pytest.raises(RuntimeError):
        service.publish(run_id)
    assert client.get(f'/api/release/{run_id}').json() == before
    with connection() as db:
        assert db.execute('SELECT COUNT(*) FROM releases WHERE run_id=?', (run_id,)).fetchone()[0] == 1
