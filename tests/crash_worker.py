"""Subprocess helper: abruptly exits at a transaction boundary."""
import os
import sys
from spatial_id.storage import Database,RecordStore

db=Database(sys.argv[1]);db.migrate()
store=RecordStore(db,ttl_ms=300);store.acquire(heartbeat=False)
if sys.argv[2]=="before":
    execute=db.execute
    def crash(c,sql,args=()):
        if sql.startswith("UPDATE allocations SET timestamp"):
            os._exit(17)
        return execute(c,sql,args)
    db.execute=crash
store.generate(dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4,vehicle_id="crash"),"crash-retry")
os._exit(17)
