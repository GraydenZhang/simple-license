"""Customer CLI; compiled into the school-permit executable."""
import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve', 'approval', 'verify', 'fingerprint', 'license-status', 'init', 'migrate', 'backup', 'maintenance'])
    args, rest = parser.parse_known_args()
    if args.command == 'fingerprint':
        from app.licensing import device_fingerprint
        print(device_fingerprint())
        return
    if args.command == 'license-status':
        from app.licensing import check_license
        from dataclasses import asdict
        state = check_license()
        print(json.dumps(asdict(state), ensure_ascii=False))
        raise SystemExit(0 if state.valid else 2)
    if args.command == 'serve':
        import os
        import signal
        import subprocess
        import time
        from pathlib import Path
        compiled = '__compiled__' in globals()
        command = [str(Path(sys.argv[0]).resolve())] if compiled else [sys.executable, str(Path(__file__).resolve())]
        children = []
        stopping = False
        def stop(*_):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            for name in ('approval', 'verify'):
                children.append(subprocess.Popen(command + [name]))
            while not stopping and all(child.poll() is None for child in children):
                time.sleep(0.25)
            failed = not stopping
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        raise SystemExit(1 if failed else 0)
    if args.command in ('approval', 'verify'):
        import os
        import uvicorn
        from app.main import approval_app, verify_app
        prefix = args.command.upper()
        uvicorn.run(approval_app if args.command == 'approval' else verify_app,
                    host=os.getenv(prefix + '_HOST', '127.0.0.1'),
                    port=int(os.getenv(prefix + '_PORT', '15001' if args.command == 'approval' else '15002')),
                    access_log=False, proxy_headers=False, server_header=False)
    elif args.command == 'migrate':
        from app.schema_migrations import migrate
        migrate()
    else:
        sys.argv = [sys.argv[0], *rest]
        if args.command == 'init':
            from scripts.init_db import main as initialize
            initialize()
        elif args.command == 'backup':
            import scripts.backup
        elif args.command == 'maintenance':
            import scripts.maintenance

if __name__ == '__main__':
    main()
