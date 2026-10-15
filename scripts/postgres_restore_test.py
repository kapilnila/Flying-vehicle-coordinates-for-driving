"""Backup/restore verification in a uniquely named disposable acceptance database."""
import os
import subprocess
import uuid


def main():
    env=dict(os.environ,TEST_TOKEN=os.environ["TEST_TOKEN"])
    base=["docker","compose","-p","spatial-id-acceptance","-f","compose.yaml","-f","compose.test.yaml","exec","-T","db"]
    def run(*args, **kwargs):
        return subprocess.run(base+list(args),env=env,check=True,capture_output=True,**kwargs).stdout
    target="restore_acceptance_"+uuid.uuid4().hex
    assert target.startswith("restore_acceptance_") and len(target)==51
    # Binary dump never goes through PowerShell text encoding.
    dump=run("pg_dump","-U","spatial","-d","spatial","-Fc")
    run("createdb","-U","spatial",target)
    try:
        run("pg_restore","-U","spatial","-d",target,"--exit-on-error",input=dump)
        sql="SELECT (SELECT count(*) FROM records),(SELECT count(*) FROM requests),(SELECT count(*) FROM allocations);"
        restored=run("psql","-U","spatial","-d",target,"-Atc",sql).decode().strip()
        counts=[int(n) for n in restored.split("|")]
        assert all(n>0 for n in counts),restored
        integrity=run("psql","-U","spatial","-d",target,"-Atc",
                      "SELECT count(*) FROM records r LEFT JOIN requests q ON r.request_key=q.request_key WHERE q.request_key IS NULL;").decode().strip()
        assert integrity=="0"
        print("PASS: PostgreSQL binary backup restored; records|requests|workers="+restored)
        print("Restored database was read-only to the test; no generators used historical allocation state.")
    finally:
        run("dropdb","-U","spatial",target)
        print("Removed only the disposable restoration database; original database and volumes retained.")


if __name__=="__main__":main()
