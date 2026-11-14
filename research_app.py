"""Authenticated local analyst UI over the existing persistent research workflow.

The browser is an operator surface, never an agent tool. Human review uses the
same exact-content gates and ephemeral portal approval as the terminal client.
Starting the server performs no model calls. One execution worker is supported.
"""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from langgraph.types import Command
from workflow_contracts import ResearchRequest, HumanDecision, Plan, Draft, WorkflowError, canonical
from workflow_agents import OpenRouterModel, load_config
from workflow_tools import MCPBackend
from research_workflow import open_workflow
from research_tools_client import credentials
from run_research_workflow import execution_lock, pending_gate, portal_review
from workflow_support import require_current_support
from company_registry import COMPANIES

ROOT = Path(__file__).resolve().parent
RUN_ID = re.compile(r'[A-Za-z0-9_-]{1,60}')
KEYS = ('OPENROUTER_API_KEY', 'SANDBOX_API_TOKEN', 'PORTAL_READ_TOKEN', 'PORTAL_DRAFT_TOKEN', 'PORTAL_PUBLISH_TOKEN', 'PORTAL_REVIEW_TOKEN')
PUBLIC_EVENT_KEYS = ('seq', 'kind', 'role', 'at')


class StartRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    run_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,60}$')
    request: ResearchRequest
    accept_model_costs: Literal[True]


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    accept_model_costs: Literal[True]


class BrowserDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['approve', 'edit', 'reject']
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    replacement: dict | None = None
    reviewer: str = Field(min_length=1, max_length=100)
    confirmation: Literal['APPROVE', 'EDIT', 'REJECT']
    accept_model_costs: bool = False


class MemoryWrite(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    value: str | None = None
    label: str | None = None
    note: str = ''
    expected_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class MemoryDelete(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class LessonDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['approve', 'reject']
    reason: str = Field(min_length=1, max_length=1000)
    confirmation: Literal['APPROVE', 'REJECT']


def safe_path(path):
    p = Path(path).absolute()
    return not any(x.is_symlink() for x in (p, *p.parents))


def bounded_json(path):
    if not safe_path(path) or not path.is_file() or path.stat().st_size > 256000:
        raise WorkflowError('invalid_saved_run', 'The saved run file is missing or unsafe.')
    return json.loads(path.read_text())


def new_json(path, value):
    if not safe_path(path):
        raise WorkflowError('unsafe_state_path', 'The state path cannot use symbolic links.')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw = canonical(value).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def load_operator_keys():
    saved = credentials()
    return {key: os.environ.get(key) or saved.get(key) for key in KEYS}


class ResearchController:
    def __init__(self, folder=None, *, key_loader=load_operator_keys, model_factory=OpenRouterModel,
                 backend_factory=MCPBackend, workflow_factory=open_workflow, review_function=portal_review):
        self.folder = Path(folder or os.environ.get('RESEARCH_UI_STATE_DIR', ROOT / 'data/workflow_final')).absolute()
        if not safe_path(self.folder):
            raise ValueError('Research state directory cannot use symbolic links.')
        self.key_loader, self.model_factory = key_loader, model_factory
        self.backend_factory, self.workflow_factory, self.review_function = backend_factory, workflow_factory, review_function
        self.task = None
        self.active_run = None
        self.errors = {}
        self.steps = {}

    def path(self, run_id, suffix):
        if not RUN_ID.fullmatch(run_id):
            raise WorkflowError('invalid_run_id', 'Use letters, digits, underscores and hyphens in the run ID.')
        return self.folder / (run_id + suffix)

    def saved(self, run_id):
        request = ResearchRequest.model_validate(bounded_json(self.path(run_id, '.request.json'))).model_dump()
        bounded_json(self.path(run_id, '.config.json'))
        config = load_config(self.path(run_id, '.config.json'))
        return request, config

    def busy(self):
        return self.task is not None and not self.task.done()

    def available(self):
        if self.busy():
            raise WorkflowError('workflow_busy', 'A workflow is running. Wait for its current execution to pause or finish.')

    def config(self):
        config = load_config()
        return {'model': config['model'], 'token_reservation_limit': config['token_budget'],
                'tool_call_limit': config['tool_budget'], 'revision_limit': config['max_revisions'],
                'model_key_available': bool(self.key_loader().get('OPENROUTER_API_KEY')),
                'companies': [{'ticker': t, 'name': c['name']} for t, c in COMPANIES.items()],
                'publication': 'Only after explicit approval of the exact checked note.',
                'scope': 'Prepared company filings for fiscal years 2023–2025; operating margins with optional qualitative risks.'}

    def list_runs(self):
        if not self.folder.exists():
            return []
        return [{'run_id': p.name.removesuffix('.request.json'), 'active': self.active_run == p.name.removesuffix('.request.json') and self.busy()}
                for p in sorted(self.folder.glob('*.request.json'), key=lambda x: x.stat().st_mtime, reverse=True)[:100]
                if not p.is_symlink() and RUN_ID.fullmatch(p.name.removesuffix('.request.json'))]

    async def status(self, run_id):
        request, config = self.saved(run_id)
        backend = self.backend_factory(self.key_loader())
        async with self.workflow_factory(self.folder, run_id, request, config, None, backend, **self.background(run_id)) as (workflow, graph, options):
            snapshot = await graph.aget_state(options)
            execution = workflow.broker.store.report(run_id)
            gate = pending_gate(snapshot)
            events = [{key: e.get(key) for key in PUBLIC_EVENT_KEYS} for e in execution['events'][-160:]]
            return {'run_id': run_id, 'request': request, 'status': snapshot.values.get('status', 'not_started'),
                    'running': self.active_run == run_id and self.busy(), 'next_nodes': list(snapshot.next),
                    'plan': snapshot.values.get('plan'), 'review': gate,
                    'verification': snapshot.values.get('verification'), 'publication': snapshot.values.get('publication'),
                    'critique': snapshot.values.get('critique'), 'budget': execution['budget'], 'metrics': execution['metrics'],
                    'events': events, 'completed_nodes': self.steps.get(run_id, [])[-100:], 'error': self.errors.get(run_id)}

    async def start(self, payload):
        self.available()
        keys = self.key_loader()
        if not keys.get('OPENROUTER_API_KEY'):
            raise WorkflowError('missing_model_key', 'Configure OPENROUTER_API_KEY before starting paid research.')
        run_id = payload.run_id
        request_path = self.path(run_id, '.request.json')
        config_path = self.path(run_id, '.config.json')
        lesson_path = self.path(run_id, '.lessons.json')
        if request_path.exists() or config_path.exists() or lesson_path.exists():
            raise WorkflowError('existing_run', 'This run already exists. Select it and continue or review it.')
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Keep the validated request and full original config for restart parity.
        with execution_lock(self.folder):
            from workflow_lessons import LESSON_TAGS
            new_json(lesson_path, self.lesson_memory().freeze(sorted(LESSON_TAGS)))
            new_json(config_path, load_config())
            new_json(request_path, payload.request.model_dump())
        self._schedule(run_id, 'start')
        return {'run_id': run_id, 'accepted': True}

    async def continue_run(self, run_id, payload):
        self.available()
        self.saved(run_id)
        current = await self.status(run_id)
        if current['review']:
            raise WorkflowError('human_review_required', 'Review the pending plan or note before continuing.')
        if not current['next_nodes'] and current['status'] != 'not_started':
            raise WorkflowError('terminal_run', 'This run has finished. Start a new run to perform new research.')
        if not self.key_loader().get('OPENROUTER_API_KEY'):
            raise WorkflowError('missing_model_key', 'The model credential is needed to continue execution.')
        self._schedule(run_id, 'continue')
        return {'run_id': run_id, 'accepted': True}

    async def pause(self, run_id):
        self.path(run_id, '.request.json')
        if not self.busy() or self.active_run != run_id:
            raise WorkflowError('not_running', 'This run is already paused or finished.')
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.active_run = None
        self.errors.setdefault(run_id, {'code': 'execution_interrupted', 'message': 'Execution stopped. Checkpoints and budget reservations remain.'})
        return {'run_id': run_id, 'paused': True, 'budget_reservations_retained': True}

    async def decide(self, run_id, payload):
        self.available()
        if payload.confirmation != payload.action.upper():
            raise WorkflowError('confirmation_mismatch', 'Type APPROVE, EDIT or REJECT for the selected decision.')
        if not payload.reviewer.strip() or any(ord(c) < 32 for c in payload.reviewer):
            raise WorkflowError('invalid_reviewer', 'Enter your reviewer name without control characters.')
        current = await self.status(run_id)
        gate = current['review']
        if not gate:
            raise WorkflowError('no_pending_review', 'There is no pending human review gate.')
        if gate['expected_hash'] != payload.expected_hash:
            raise WorkflowError('stale_review', 'The content changed. Refresh and review the current version.')
        if payload.action != 'reject' and not payload.accept_model_costs:
            raise WorkflowError('model_cost_consent_required', 'Resuming approval or editing can make model calls. Confirm the displayed budget.')
        if payload.action != 'reject' and not self.key_loader().get('OPENROUTER_API_KEY'):
            raise WorkflowError('missing_model_key', 'The model credential is needed to resume this decision.')
        replacement = payload.replacement
        if payload.action == 'edit':
            schema = Plan if gate['gate'] == 'plan' else Draft
            replacement = schema.model_validate(replacement).model_dump()
        elif replacement is not None:
            raise WorkflowError('unexpected_replacement', 'Only edit accepts replacement content.')
        decision = HumanDecision(action=payload.action, expected_hash=payload.expected_hash, replacement=replacement)
        self._schedule(run_id, 'review', decision=decision.model_dump(), reviewer=payload.reviewer.strip())
        return {'run_id': run_id, 'accepted': True, 'action': payload.action}

    def _schedule(self, run_id, command, **kwargs):
        self.available()
        self.active_run = run_id
        self.errors.pop(run_id, None)
        self.task = asyncio.create_task(self._execute(run_id, command, **kwargs))

    async def _execute(self, run_id, command, *, decision=None, reviewer=None):
        try:
            with execution_lock(self.folder):
                request, config = self.saved(run_id)
                keys = self.key_loader()
                backend = self.backend_factory(keys)
                model = self.model_factory(keys['OPENROUTER_API_KEY']) if keys.get('OPENROUTER_API_KEY') else None
                async with self.workflow_factory(self.folder, run_id, request, config, model, backend, **self.background(run_id)) as (workflow, graph, options):
                    snapshot = await graph.aget_state(options)
                    gate = pending_gate(snapshot)
                    if command == 'review':
                        if not gate or gate['expected_hash'] != decision['expected_hash']:
                            raise WorkflowError('stale_review', 'The pending content changed before execution. Review it again.')
                        if gate['gate'] == 'note':
                            if decision['action'] == 'approve':
                                require_current_support(snapshot.values, workflow.broker.store.evidence(run_id))
                            action = 'reject' if decision['action'] == 'edit' else decision['action']
                            # The review capability is kept only on this Workflow object.
                            # It never enters the browser, model, journal or checkpoints.
                            workflow.approval = await self.review_function(backend, keys, gate, action, reviewer)
                        workflow.broker.store.event(run_id, 'browser_human_decision', 'human',
                                                    {'gate': gate['gate'], 'action': decision['action'], 'content_hash': decision['expected_hash']})
                        value = Command(resume=decision)
                    elif gate:
                        raise WorkflowError('human_review_required', 'A human decision is pending.')
                    elif snapshot.values and not snapshot.next:
                        return
                    else:
                        value = {'request': request, 'researchers': {}, 'revisions': 0, 'replans': 0} if not snapshot.values else None
                    async for update in graph.astream(value, options, stream_mode='updates'):
                        nodes = [k for k in update if k != '__interrupt__']
                        self.steps.setdefault(run_id, []).extend(nodes)
                        self.steps[run_id] = self.steps[run_id][-100:]
        except asyncio.CancelledError:
            self.errors[run_id] = {'code': 'execution_interrupted', 'message': 'Execution stopped. Saved checkpoints and budget reservations remain; inspect before continuing.'}
            raise
        except WorkflowError as exc:
            self.errors[run_id] = {'code': exc.code, 'message': 'Workflow paused or stopped at a checked boundary. Saved state and budget reservations remain. Inspect the current plan, critique and event log.'}
        except Exception:
            self.errors[run_id] = {'code': 'execution_failed', 'message': 'Execution did not finish. Check service health and saved state. No credential or raw error was returned.'}
        finally:
            self.active_run = None

    async def close(self):
        if self.busy():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def analyst_memory(self):
        from workflow_memory import AnalystMemory
        return AnalystMemory(self.folder / 'analyst_memory.sqlite3', 'local-analyst')

    def lesson_memory(self):
        from workflow_lessons import LessonStore
        return LessonStore(self.folder / 'lessons.sqlite3', 'local-analyst')

    def background(self, run_id):
        return {'memory': self.analyst_memory(), 'lesson_store': self.lesson_memory(),
                'lesson_manifest': bounded_json(self.path(run_id, '.lessons.json'))}


def token_from_file():
    path = Path(os.environ.get('ANALYST_UI_TOKEN_FILE', ROOT / 'data/research_app/access_token'))
    if not safe_path(path) or not path.is_file() or path.stat().st_size > 256:
        return None
    value = path.read_text().strip()
    return value if len(value) >= 32 and value.isascii() else None


def create_app(controller=None, *, token_loader=token_from_file, allowed_hosts=None):
    control = controller or ResearchController()
    @asynccontextmanager
    async def lifespan(application):
        yield
        await control.close()
    application = FastAPI(title='Analyst Desk Research', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ['127.0.0.1', 'localhost'])
    application.state.controller = control

    @application.middleware('http')
    async def protect(request, call_next):
        if request.url.path.startswith('/api/'):
            expected = token_loader()
            supplied = request.headers.get('authorization', '')
            if not expected or not supplied.startswith('Bearer ') or not supplied[7:].isascii() or not secrets.compare_digest(supplied[7:], expected):
                return JSONResponse({'error': {'code': 'unauthorized', 'message': 'Unlock this local session with the UI access token.'}}, status_code=401)
            origin = request.headers.get('origin')
            if origin and origin not in {'http://' + request.headers.get('host', ''), 'https://' + request.headers.get('host', '')}:
                return JSONResponse({'error': {'code': 'origin_rejected', 'message': 'Cross-origin operator requests are refused.'}}, status_code=403)
            length = request.headers.get('content-length', '0')
            if not length.isdigit() or int(length) > 128000:
                return JSONResponse({'error': {'code': 'request_too_large', 'message': 'The request exceeds the operator limit.'}}, status_code=413)
            if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 128000:
                        return JSONResponse({'error': {'code': 'request_too_large', 'message': 'The request exceeds the operator limit.'}}, status_code=413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        html = (ROOT / 'research_ui.html').read_text()
        hashes = ['\'sha256-' + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + '\'' for s in re.findall(r'<script>(.*?)</script>', html, re.S)]
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src " + ' '.join(hashes) + "; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @application.exception_handler(WorkflowError)
    async def workflow_error(request, exc):
        code = 409 if exc.code in {'workflow_busy', 'stale_review', 'existing_run', 'human_review_required', 'no_pending_review', 'terminal_run'} else 400
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message}}, status_code=code)

    @application.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': {'code': 'invalid_input', 'message': 'The replacement does not match the displayed plan or draft schema.'}}, status_code=422)

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        return JSONResponse({'error': {'code': 'invalid_input', 'message': 'Check the required fields, allowed scope and explicit confirmation. No input content was returned in this error.'}}, status_code=422)

    @application.get('/health')
    async def health():
        return {'status': 'ok', 'service': 'analyst-desk-ui'}

    @application.get('/', response_class=HTMLResponse)
    async def index():
        return HTMLResponse((ROOT / 'research_ui.html').read_text())

    @application.get('/api/config')
    async def configuration():
        return control.config()

    @application.get('/api/runs')
    async def runs():
        return {'runs': control.list_runs()}

    @application.post('/api/runs', status_code=202)
    async def start(payload: StartRequest):
        return await control.start(payload)

    @application.get('/api/runs/{run_id}')
    async def status(run_id: str):
        return await control.status(run_id)

    @application.post('/api/runs/{run_id}/continue', status_code=202)
    async def resume(run_id: str, payload: ContinueRequest):
        return await control.continue_run(run_id, payload)

    @application.post('/api/runs/{run_id}/pause')
    async def pause(run_id: str):
        return await control.pause(run_id)

    @application.post('/api/runs/{run_id}/decision', status_code=202)
    async def decision(run_id: str, payload: BrowserDecision):
        return await control.decide(run_id, payload)

    @application.get('/api/memory')
    async def memory():
        saved = control.analyst_memory()
        return {kind: saved.read(kind) for kind in ('preferences', 'watchlist', 'coverage', 'published_notes')}

    @application.put('/api/memory/{kind}/{key}')
    async def memory_write(kind: str, key: str, payload: MemoryWrite):
        saved = control.analyst_memory()
        if kind == 'preferences':
            return saved.set_preference(key, payload.value, expected_hash=payload.expected_hash)
        if kind == 'watchlist':
            return saved.set_watchlist(key, payload.label, payload.note, expected_hash=payload.expected_hash)
        raise WorkflowError('memory_write_forbidden', 'Only explicit preferences and private watchlist entries can be entered manually.')

    @application.delete('/api/memory/{kind}/{key}')
    async def memory_delete(kind: str, key: str, payload: MemoryDelete):
        return control.analyst_memory().delete(kind, key, payload.expected_hash)

    @application.get('/api/lessons')
    async def lessons():
        return {'lessons': control.lesson_memory().list()}

    @application.post('/api/lessons/{lesson_id}/review')
    async def lesson_review(lesson_id: str, payload: LessonDecision):
        control.available()
        if payload.confirmation != payload.action.upper():
            raise WorkflowError('confirmation_mismatch', 'Confirm the selected lesson decision explicitly.')
        return control.lesson_memory().review(lesson_id, payload.expected_hash, payload.action,
                                              payload.reason, human_confirmed=True)

    @application.get('/api/runs/{run_id}/events')
    async def events(run_id: str, request: Request):
        await control.status(run_id)
        async def stream():
            last = None
            for _ in range(900):
                if await request.is_disconnected():
                    break
                snapshot = await control.status(run_id)
                raw = canonical(snapshot)
                current = hashlib.sha256(raw.encode()).hexdigest()
                if current != last:
                    yield 'event: status\ndata: ' + raw + '\n\n'
                    last = current
                else:
                    yield ': heartbeat\n\n'
                if not snapshot.get('running'):
                    yield 'event: idle\ndata: {}\n\n'
                    break
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    return application


app = create_app()
