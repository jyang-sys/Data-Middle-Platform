"""Run the isolated Base test environment using its untracked server configuration."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def configure(path):
    config=json.loads(path.read_text())
    token=config.get('BASELINKER_API_TOKEN','')
    db=Path(config['DATA_PLATFORM_DB']).resolve()
    if not token or config.get('BASE_DATA_ENVIRONMENT')!='test' or db==ROOT/'data'/'platform.sqlite3' or 'environments/base-test' not in str(db):
        raise SystemExit('测试配置缺失或数据库隔离不正确。')
    os.environ.update(BASELINKER_API_TOKEN=token,DATA_PLATFORM_DB=str(db),BASE_DATA_ENVIRONMENT='test')
    sys.path.insert(0,str(ROOT))
    from app import main
    main.initialize()
    with main.database() as connection:connection.execute("INSERT OR REPLACE INTO app_metadata VALUES('data_mode','base_test')")


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['web','sync','worker'])
    parser.add_argument('--port',type=int,default=8003)
    parser.add_argument('--config',type=Path,default=ROOT/'data/environments/base-test/runtime.json')
    args=parser.parse_args()
    configure(args.config)
    if args.mode=='web':
        import uvicorn
        uvicorn.run('app.main:app',host='127.0.0.1',port=args.port)
    elif args.mode=='sync':
        from app.worker import cycle
        raise SystemExit(0 if cycle() else 1)
    else:
        from app.worker import main
        sys.argv=[sys.argv[0]]
        raise SystemExit(main())
