"""Operations for migrations, proxy startup, and SQLite backup."""
import argparse
import os
from pathlib import Path
import sqlite3
import time
from .storage import Database

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate","init-proxy","backup"])
    parser.add_argument("--database-url",default=os.getenv("DATABASE_URL","sqlite:///.spatial-id/state.db"))
    parser.add_argument("--output")
    args=parser.parse_args()
    if args.command=="init-proxy":
        from .test_lab import Lab
        for attempt in range(30):
            try:
                Lab().reset_fault(); return
            except Exception:
                if attempt==29: raise
                time.sleep(1)
    db=Database(args.database_url)
    try:
        if args.command=="migrate": db.migrate()
        else:
            if db.pg or not args.output:
                parser.error("backup requires SQLite and a new --output path; use pg_dump for PostgreSQL")
            path=Path(args.output)
            if path.exists(): parser.error("output already exists")
            with db.connection() as source:
                target=sqlite3.connect(path)
                try: source.backup(target)
                finally: target.close()
    finally: db.close()

if __name__=="__main__": main()
