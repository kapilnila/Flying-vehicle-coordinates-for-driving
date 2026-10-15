import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from spatial_id.storage import Database,RecordStore

@pytest.mark.parametrize("boundary",["before","after"])
def test_abrupt_exit_durability(tmp_path,boundary):
    path=str(tmp_path/"crash.db")
    environment=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process=subprocess.run([sys.executable,str(Path(__file__).with_name("crash_worker.py")),path,boundary],env=environment,timeout=30)
    assert process.returncode==17
    time.sleep(.35)
    db=Database(path);db.migrate()
    store=RecordStore(db);store.acquire(heartbeat=False)
    try:
        result,replay=store.generate(dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4,vehicle_id="crash"),"crash-retry")
        assert replay==(boundary=="after")
        assert len(store.history("crash")["items"])==1
    finally: store.close()
