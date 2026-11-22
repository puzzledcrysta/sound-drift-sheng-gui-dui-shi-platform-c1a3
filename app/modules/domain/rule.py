from datetime import date
from decimal import Decimal as D
from app.errors import DataError
from .values import number, whole, parts

DAY = D(86400000)
MISSING = {'', '-', 'n/a', 'null', '未知'}


def crossings(text):
    """Parse the optional cross-day record.

    Entries are separated by semicolons.  Each entry is either a sample
    number (``48000``), a calendar date (``2026-11-19``) or both
    (``48000:2026-11-19``).  A sample pins the anchor boundary where the
    reference clock restarted; a bare date only asserts that the recording
    crossed into that day, so its boundary is matched against the observed
    clock drops in order.
    """
    text = (text or '').strip()
    if not text or text.casefold() in MISSING or text == '无':
        return []
    entries, samples = [], set()
    for item in text.split(';'):
        item = item.strip()
        if not item:
            raise DataError('SOUND_WRAP', '跨日记录含空项')
        sample_text, sep, day_text = item.partition(':')
        sample, day = None, None
        if sep:
            sample = whole(sample_text, 0, 10**16)
            try:
                day = date.fromisoformat(day_text.strip()).isoformat()
            except ValueError as error:
                raise DataError('DATE', f'跨日记录日期无效 {item[:80]}') from error
        elif '-' in item:
            try:
                day = date.fromisoformat(item).isoformat()
            except ValueError as error:
                raise DataError('DATE', f'跨日记录日期无效 {item[:80]}') from error
        else:
            sample = whole(item, 0, 10**16)
        if sample is not None:
            if sample in samples:
                raise DataError('SOUND_WRAP', f'跨日记录样本 {sample} 重复')
            samples.add(sample)
        entries.append((sample, day))
    return entries


def evaluate(row):
    rate = whole(row['采样率赫兹'], 1, 384000)
    max_ppm = number(row['允许漂移ppm'], 0, 100000)
    start = whole(row['片段始样本'], 0, 10**16)
    end = whole(row['片段末样本'], start+1, 10**16)
    anchors = {}
    for item in parts(row['锚点']):
        count, sep, milliseconds = item.partition(':')
        if not sep:
            raise DataError('SOUND_ANCHOR', '锚点须包含样本编号与参考毫秒')
        count, milliseconds = whole(count, 0, 10**16), number(milliseconds, 0, 10**16)
        if count in anchors and anchors[count] != milliseconds:
            raise DataError('SOUND_ANCHOR', f'样本 {count} 有不同参考时间')
        anchors[count] = milliseconds
    points = sorted(anchors.items())
    if len(points) < 2 or not points[0][0] <= start < end <= points[-1][0]:
        raise DataError('SOUND_COVERAGE', '片段超出锚点覆盖区间')

    spans = list(zip(points, points[1:]))
    drops = [t2 <= t1 for (_,t1),(_,t2) in spans]
    days = [0]*len(spans)
    dated = [[] for _ in spans]
    pending = []
    # A sample-scoped record must line up with an actual clock drop.
    for sample, day in crossings(row.get('跨日记录','')):
        if sample is None:
            pending.append(day)
            continue
        for index, ((a,_),(b,_)) in enumerate(spans):
            if a < sample <= b:
                if days[index] >= 1:
                    raise DataError('SOUND_WRAP', f'锚段 {a}→{b} 有多条跨日记录')
                days[index] += 1
                dated[index].append((sample, day))
                break
        else:
            raise DataError('SOUND_WRAP', f'跨日样本 {sample} 不在锚点区间')
        if not drops[index]:
            raise DataError('SOUND_WRAP', f'跨日样本 {sample} 处钟面并未回退')
    # A date-only record is only usable when it can be matched to a drop that
    # nothing else already explains.
    free = [index for index, drop in enumerate(drops) if drop and not days[index]]
    if len(pending) > len(free):
        raise DataError('SOUND_WRAP', '跨日记录多过钟面可见的回退')
    for index, day in zip(free, pending):
        days[index] += 1
        dated[index].append((None,day))

    duration, proof = D(0), []
    offset = 0
    for index, ((a,t1),(b,t2)) in enumerate(spans):
        before = offset
        offset += days[index]
        if drops[index] and not days[index]:
            raise DataError('SOUND_REWIND', f'锚点 {a}→{b} 出现时钟回退')
        upper, lower = t2 + DAY*offset, t1 + DAY*before
        if upper <= lower:
            raise DataError('SOUND_WRAP', f'跨日记录无法解释锚点 {a}→{b} 的回退')
        ppm = ((upper-lower)*rate / ((b-a)*1000)-1)*10**6
        if abs(ppm) > max_ppm:
            raise DataError('SOUND_PPM', f'锚点 {a}→{b} 漂移超限')
        left,right = max(a,start),min(b,end)
        if left < right:
            slope = (upper-lower)/(b-a)
            duration += slope*(right-left)
            proof.append((f'{left}–{right} 样本',f'{slope*(right-left):.3f}','毫秒'))
            proof.append((f'{a}–{b} 锚段',f'{ppm:.3f}','ppm'))
            if days[index]:
                proof.append((f'{a}–{b} 跨日',f'+{days[index]*86400000}','毫秒'))
                for sample, day in dated[index]:
                    if sample is not None:
                        proof.append((f'跨日样本 {sample}',day or '钟面归零处',''))
                    else:
                        proof.append(('跨日日期',day,''))
    return duration.quantize(D('0.001')), proof
