"""One dedicated process periodically reconciles every enabled Base data domain."""
import argparse
import logging
import os
import time

from app.main import initialize, database
from app.ingestion import DOMAINS, run_sync, now


def recover_interrupted():
    # Run only after checking no old worker/API synchronization is still alive.
    with database() as db:
        db.execute("UPDATE base_sync_jobs SET status='failed',finished_at=?,error=? WHERE status='running'",
                   (now(), '进程中断，需重新同步。'))


def cycle():
    failed = False
    for domain in DOMAINS:
        try:
            job = run_sync(domain)
            logging.info("domain=%s status=success job=%s", domain, job)
        except Exception:
            failed = True
            logging.error("domain=%s status=failed; inspect /api/base/status", domain)
    return not failed


def main():
    parser = argparse.ArgumentParser(description="Base 数据自动同步进程")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--recover-interrupted", action="store_true", help="确认旧任务进程已停止后释放中断任务")
    args = parser.parse_args()
    interval = int(os.environ.get("BASE_SYNC_INTERVAL_SECONDS", "900"))
    if interval < 60:
        parser.error("BASE_SYNC_INTERVAL_SECONDS 至少为 60")
    initialize()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.recover_interrupted:
        recover_interrupted()
    while True:
        succeeded = cycle()
        if args.once:
            return 0 if succeeded else 1
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
