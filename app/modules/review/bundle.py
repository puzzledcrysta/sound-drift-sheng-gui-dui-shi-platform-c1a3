import json
from app.modules.runs.service import get
from app.modules.release.service import fetch
from .decisions import timeline


def bundle(run_id):
    history = timeline(run_id)
    run = get(run_id)
    release = fetch(run_id)
    # The export carries source line evidence, the applicable decision version
    # and the per-segment release conclusion handed to the vendor.
    return json.dumps({'manifest':run['manifest'],'counts':run['counts'],'results':run['accepted'],
                       'quarantine':run['quarantine'],'suppressed':run['suppressed'],
                       'reconciliation':run['book'],'release':release,'review':history},
                      ensure_ascii=False,indent=2)
