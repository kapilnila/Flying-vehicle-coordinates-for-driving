"""Compare packing and durable batches without weakening storage guarantees."""
import argparse
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from spatial_id import SpatialIdGenerator
from spatial_id.storage import Database,RecordStore

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--count",type=int,default=10000)
    parser.add_argument("--batch",type=int,default=100)
    args=parser.parse_args()
    if args.count<1 or not 1<=args.batch<=1000: parser.error("invalid count or batch")
    results=[]
    with tempfile.TemporaryDirectory() as directory:
        modes=[("memory",None),("sqlite",str(Path(directory)/"bench.db"))]
        if args.database_url: modes.append(("postgresql",args.database_url))
        for mode,url in modes:
            samples=[];values=[]
            generator=SpatialIdGenerator(999)
            store=None
            if url:
                db=Database(url);db.migrate();store=RecordStore(db,999);store.acquire()
            start=time.perf_counter()
            try:
                for offset in range(0,args.count,args.batch):
                    count=min(args.batch,args.count-offset);begin=time.perf_counter()
                    if store:
                        response,_=store.generate(dict(latitude=1,longitude=2,altitude_m=3,speed_mps=4,count=count,vehicle_id="benchmark"))
                        values.extend(response["ids"])
                    else: values.extend(generator.generate(1,2,3,4) for _ in range(count))
                    samples.append((time.perf_counter()-begin)*1000)
                elapsed=time.perf_counter()-start
            finally:
                if store: store.close()
            samples.sort()
            results.append(dict(mode=mode,count=len(values),batch=args.batch,seconds=elapsed,ids_per_second=len(values)/elapsed,
                duplicates=len(values)-len(set(values)),batch_latency_ms={p:samples[int((len(samples)-1)*q)] for p,q in [('p50',.5),('p95',.95),('p99',.99)]}))
    print(json.dumps(dict(platform=platform.platform(),python=platform.python_version(),results=results),indent=2))

if __name__=="__main__":main()
