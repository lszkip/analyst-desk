"""Start the complete local Docker stack without starting paid research.

This command preserves existing data and volumes. It may stop the old, named
monitoring stack to hand its loopback ports and persistent volumes to the final
stack. It never deletes a volume or reads workflow databases.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zipfile import ZipFile, ZIP_DEFLATED

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LOCAL_KEYS = ('QDRANT_API_KEY', 'QDRANT_READ_ONLY_API_KEY', 'SANDBOX_API_TOKEN', 'PORTAL_READ_TOKEN',
              'PORTAL_DRAFT_TOKEN', 'PORTAL_PUBLISH_TOKEN', 'PORTAL_REVIEW_TOKEN')
CHECKS = {'research_ui': 'http://127.0.0.1:8080/health', 'api': 'http://127.0.0.1:8000/health',
          'edgar': 'http://127.0.0.1:8001/health', 'filings_kb': 'http://127.0.0.1:8002/ready',
          'python_sandbox': 'http://127.0.0.1:8003/ready', 'research_portal': 'http://127.0.0.1:8004/health',
          'monitoring': 'http://127.0.0.1:8085/__monitoring_health', 'prometheus': 'http://127.0.0.1:9090/-/ready',
          'grafana': 'http://127.0.0.1:3000/api/health'}


def safe(path):
    path = Path(path).absolute()
    return not any(p.is_symlink() for p in (path, *path.parents))


def atomic_new(path, raw, mode=0o600):
    if not safe(path):
        raise ValueError('A setup path uses a symbolic link.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def setup(root):
    env_path = root / '.env'
    if not safe(env_path) or (env_path.exists() and env_path.stat().st_size > 128000):
        raise ValueError('The local environment file is unsafe or too large.')
    values = dict(dotenv_values(env_path)) if env_path.exists() else {}
    if not values.get('OPENROUTER_API_KEY'):
        raise ValueError('Add OPENROUTER_API_KEY to the existing local .env file first. Its value must never be uploaded or pasted into chat.')
    additions = {}
    for key in LOCAL_KEYS:
        if key not in values:
            additions[key] = secrets.token_urlsafe(32)
        elif not values[key] or not values[key].isascii() or len(values[key]) < 32:
            raise ValueError('An existing local service credential is invalid; no existing value was replaced: ' + key)
    combined = {**values, **additions}
    if len({combined[k] for k in LOCAL_KEYS}) != len(LOCAL_KEYS):
        raise ValueError('Local service credentials must be distinct; existing values were not replaced.')
    for key, value in (('ANALYST_UID', os.getuid()), ('ANALYST_GID', os.getgid())):
        if key not in values:
            additions[key] = str(value)
        elif not str(values[key]).isdigit():
            raise ValueError('The configured Docker user ID is invalid: ' + key)
    if additions:
        original = env_path.read_bytes() if env_path.exists() else b''
        raw = original + (b'\n' if original and not original.endswith(b'\n') else b'') + ''.join(k+'='+v+'\n' for k,v in additions.items()).encode()
        temporary = env_path.with_name('.env.final34_' + secrets.token_hex(4))
        atomic_new(temporary, raw)
        if (env_path.read_bytes() if env_path.exists() else b'') != original:
            temporary.unlink()
            raise ValueError('The environment file changed during setup; no update was installed.')
        os.replace(temporary, env_path)
    env_path.chmod(0o600)
    for name in ('data/research_app', 'data/workflow_final', 'data/research_portal', 'data/qdrant_storage',
                 'data/qdrant_snapshots', 'monitoring/data', 'monitoring/secrets'):
        p = root / name
        if not safe(p):
            raise ValueError('A private state folder uses a symbolic link.')
        p.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_path = root / 'data/research_app/access_token'
    if not token_path.exists():
        atomic_new(token_path, (secrets.token_urlsafe(40)+'\n').encode())
    if not safe(token_path) or token_path.stat().st_size > 256 or len(token_path.read_text().strip()) < 32:
        raise ValueError('The UI access-token file is invalid. It was not replaced.')
    token_path.chmod(0o600)
    grafana_secret = root / 'monitoring/secrets/grafana_admin_password'
    if not grafana_secret.exists():
        # Grafana UID 472 needs this read-only mounted file; it is not a model key.
        atomic_new(grafana_secret, (secrets.token_urlsafe(32)+'\n').encode(), mode=0o444)
    if not safe(grafana_secret) or not grafana_secret.is_file():
        raise ValueError('The Grafana secret path is unsafe.')
    return dict(dotenv_values(env_path))


def prerequisites(root):
    problems = []
    for name in ('Dockerfile', 'compose.final.yaml', 'retrieval_gateway.conf', 'research_app.py', 'research_ui.html',
                 'monitoring/compose.yaml', 'monitoring/gateway.conf', 'data/retrieval/manifest.json'):
        p = root / name
        if not safe(p) or not p.is_file():
            problems.append('Missing or unsafe required file: '+name)
    for name in ('data/raw', 'data/filings', 'data/passages', 'data/models'):
        p = root / name
        if not safe(p) or not p.is_dir() or not any(p.iterdir()):
            problems.append('Prepared data directory is missing or empty: '+name)
    if shutil.which('docker') is None:
        problems.append('Docker is unavailable in this shell. Start Docker Desktop with WSL integration.')
    return problems


def scrub(text, values):
    for value in sorted((str(v) for v in values.values() if v and len(str(v)) >= 16), key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    return text


def command(args, root, values, record, timeout=1200):
    started = time.monotonic()
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(args, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            last_notice = started
            while child.poll() is None:
                if time.monotonic()-started > timeout or os.fstat(log.fileno()).st_size > 8*1024*1024:
                    raise TimeoutError('Docker startup exceeded its bounded time or output limit.')
                if time.monotonic()-last_notice > 20:
                    print('Docker setup is still running ('+str(int(time.monotonic()-started))+' seconds)...', flush=True)
                    last_notice = time.monotonic()
                time.sleep(.25)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
            log.seek(0)
            raw = log.read(8*1024*1024).decode(errors='replace')
            record.setdefault('commands', []).append({'argv': args, 'returncode': child.returncode,
                'seconds': round(time.monotonic()-started, 2), 'output': scrub(raw, values)})
        if child.returncode:
            raise ValueError('A Docker command failed. Upload the startup results ZIP; local credentials are redacted.')
        return raw


def probe(url):
    # Fixed loopback URLs only. Proxy environment and redirects are not used.
    from urllib.request import ProxyHandler, HTTPRedirectHandler, build_opener
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(url), timeout=15) as response:
            return {'healthy': response.status == 200, 'http_status': response.status}
    except HTTPError as exc:
        # Do not read or export the response body, headers or exception text.
        status = exc.code
        exc.close()
        return {'healthy': False, 'http_status': status, 'error_category': 'http_error'}
    except URLError as exc:
        category = ('connection_refused' if isinstance(exc.reason, ConnectionRefusedError)
                    else 'timeout' if isinstance(exc.reason, TimeoutError) else 'connection_error')
        return {'healthy': False, 'error_category': category}
    except TimeoutError:
        return {'healthy': False, 'error_category': 'timeout'}
    except OSError:
        return {'healthy': False, 'error_category': 'connection_error'}
    except Exception:
        return {'healthy': False, 'error_category': 'probe_error'}


def new_report_directory(root, requested, stamp):
    output = Path(requested).absolute() if requested else root / 'LSOutput' / ('39_Analyst_Desk_Startup_'+stamp)
    if not safe(output):
        raise ValueError('The output directory cannot use symbolic links.')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check existing prerequisites without changing credentials or starting containers.')
    parser.add_argument('--show-token', action='store_true', help='Print only the local UI token for operator login; do not share the output.')
    parser.add_argument('--report-dir', help='Write Startup_Report.json into this new directory for the enclosing operator; omit a separate startup ZIP.')
    args = parser.parse_args()
    if args.show_token:
        path = ROOT / 'data/research_app/access_token'
        if not safe(path) or not path.is_file() or path.stat().st_size > 256:
            raise SystemExit('Run startup first; a safe UI token is not available.')
        print(path.read_text().strip())
        return 0
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+secrets.token_hex(4)
    output = new_report_directory(ROOT, args.report_dir, stamp)
    record = {'sequence':39,'operation':'complete_local_stack_startup','started_utc':datetime.now(timezone.utc).isoformat(),
              'status':'not_started','external_model_calls':0,'research_started':False,'volumes_deleted':False,'check_only':args.check}
    values = {}
    result = 1
    try:
        problems = prerequisites(ROOT)
        if problems:
            record['prerequisite_problems'] = problems
            raise ValueError('Prerequisites are incomplete: '+'; '.join(problems))
        values = dict(dotenv_values(ROOT / '.env'))
        if not args.check:
            values = setup(ROOT)
        base = ['docker','compose','--project-directory',str(ROOT),'-f',str(ROOT/'compose.final.yaml')]
        command(['docker','info','--format','{{.ServerVersion}}'], ROOT, values, record, timeout=30)
        command([*base,'config','--quiet'], ROOT, values, record, timeout=30)
        if args.check:
            record['status']='prerequisites_checked_only'
            print('Prerequisite and Compose checks passed. Containers were not started.', flush=True)
        else:
            print('Starting the complete local stack. Existing data and volumes are preserved. No research or model call starts automatically.', flush=True)
            old = ['docker','compose','--project-directory',str(ROOT/'monitoring'),'-f',str(ROOT/'monitoring/compose.yaml')]
            running = command([*old,'ps','--status','running','--quiet'], ROOT, values, record, timeout=30)
            if running.strip():
                print('Handing monitoring ports to the final stack; its existing persistent volumes are retained.', flush=True)
                command([*old,'stop'], ROOT, values, record, timeout=90)
                record['previous_monitoring_stopped_without_deleting_volumes']=True
            command([*base,'up','--detach','--build','--wait','--wait-timeout','240'], ROOT, values, record)
            record['health']={name:probe(url) for name,url in CHECKS.items()}
            record['failed_service_names']=[name for name,row in record['health'].items() if not row['healthy']]
            if record['failed_service_names']:
                raise ValueError('Local service health checks failed: '+', '.join(record['failed_service_names'])+'. See Startup_Report.json in the results ZIP.')
            record['status']='all_local_services_healthy'
            print('Research UI: http://127.0.0.1:8080\nMonitoring: http://127.0.0.1:3000', flush=True)
            print('To obtain the separate UI login token, run: python start_analyst_desk.py --show-token\nDo not upload or share that token. Model API keys remain on the server.', flush=True)
        result=0
    except KeyboardInterrupt:
        record['status']='interrupted'
        print('Startup interrupted. Existing data and volumes remain.', flush=True)
    except Exception as exc:
        record['status']='incomplete'
        record['error']={'type':type(exc).__name__,'message':scrub(str(exc),values) if isinstance(exc,(ValueError,TimeoutError)) else 'Startup did not finish; no raw credential diagnostic was returned.'}
        print(record['error']['message'], flush=True)
    finally:
        record['finished_utc']=datetime.now(timezone.utc).isoformat()
        raw=(json.dumps(record,indent=2,ensure_ascii=False)+'\n').encode()
        report=output/'Startup_Report.json'
        atomic_new(report,raw)
        if not args.report_dir:
            archive=output/('39_Analyst_Desk_Startup_Results_'+stamp+'.zip')
            with ZipFile(archive,'x',ZIP_DEFLATED) as z:
                z.writestr('Startup_Report.json',raw)
            print('Upload this startup results ZIP:\n'+str(archive),flush=True)
        else:
            print('Startup diagnostics will be included in the enclosing Sequence 39 results ZIP.', flush=True)
    return result


if __name__=='__main__':
    raise SystemExit(main())
