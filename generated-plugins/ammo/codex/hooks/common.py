from __future__ import annotations
import json,os,re,sqlite3,subprocess,sys
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any
# Plugin layout: <plugin>/codex/hooks/common.py. Shared scripts live in
# <plugin>/scripts (the resolution layer campaign_paths.py among them), the
# Codex-only ones in <plugin>/codex/scripts. campaign_paths is imported by path,
# the way hook_cmd_classify is below, and it is guarded the same way: every
# Codex hook imports this module, so a missing layer must degrade, not raise.
# Without the layer no marker is read, so outside AMMO_ARTIFACT_DIR there is no
# campaign and every hook stays silent (fail-open, the conventions section 7
# direction for "no campaign").
_PLUGIN_ROOT=Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR=_PLUGIN_ROOT/'scripts'
_CODEX_SCRIPTS_DIR=_PLUGIN_ROOT/'codex'/'scripts'
if str(_SCRIPTS_DIR) not in sys.path: sys.path.insert(0,str(_SCRIPTS_DIR))
try:
    import campaign_paths
except Exception as _layer_exc:
    campaign_paths=None
    try:
        sys.stderr.write(
            'AMMO hooks DEGRADED: '+str(_SCRIPTS_DIR)+'/campaign_paths.py unavailable ('
            +(str(_layer_exc) or _layer_exc.__class__.__name__)+'). Only AMMO_ARTIFACT_DIR '
            'binds a campaign; the .ammo/active marker is not read.\n'
        )
    except Exception:
        pass
# The campaign workspace dir name (".ammo"), from the layer's marker path.
CAMPAIGN_SEGMENT=campaign_paths.MARKER.parent.name if campaign_paths is not None else '.ammo'
def plugin_path(*parts):
    """A file of this plugin: the layer's plugin_path(), or the same root without it."""
    if campaign_paths is not None: return Path(campaign_paths.plugin_path(*parts))
    return _PLUGIN_ROOT.joinpath(*parts)
def script_path(name):
    """A shared script under <plugin>/scripts."""
    return plugin_path('scripts',name)
def codex_script_path(name):
    """A Codex-only script under <plugin>/codex/scripts."""
    return plugin_path('codex','scripts',name)
# Role addresses (references/harness-binding.md): agents are addressed as
# `ammo:<role>` on both harnesses, and each alias pair names one role.
ROLE_PREFIX='ammo:'
ROLE_ALIASES={'ammo-impl-champion':'ammo-implementer','ammo-report-writer':'ammo-report'}
def normalize_role(value):
    """One role name for comparison: no `ammo:` prefix, alias spellings folded."""
    role=str(value or '').strip()
    if role.startswith(ROLE_PREFIX): role=role[len(ROLE_PREFIX):]
    return ROLE_ALIASES.get(role,role)
# The roles a hook infers from a spawn, in match order.
_MARKED_ROLES=('ammo-implementer','ammo-auditor','ammo-transcript-monitor')
_ROLE_MARKERS=None
def role_markers():
    """Role -> (role-doc markers, agent file names), read from the shipped codex/agents/*.toml.

    A dispatch message that names a role doc (agents/<doc>.md), or an agent_path
    whose file name is a roster entry, identifies the role. Each TOML names its
    role doc; text search, because Python 3.9 hosts have no tomllib.
    """
    global _ROLE_MARKERS
    if _ROLE_MARKERS is not None: return _ROLE_MARKERS
    found={role:(set(),set()) for role in _MARKED_ROLES}
    try: roster=sorted(plugin_path('codex','agents').glob('*.toml'))
    except OSError: roster=[]
    for toml in roster:
        role=normalize_role(toml.stem)
        if role not in found: continue
        docs,names=found[role]
        names.update({toml.name,toml.stem+'.md'})
        try: text=toml.read_text(encoding='utf-8')
        except OSError: continue
        docs.update(re.findall(r'agents/ammo-[a-z0-9-]+\.md',text))
    for role,(docs,names) in found.items():
        if not names: names.update({role+'.toml',role+'.md'})
        if not docs: docs.add('agents/'+role+'.md')
    _ROLE_MARKERS={role:(frozenset(docs),frozenset(names)) for role,(docs,names) in found.items()}
    return _ROLE_MARKERS
# The ammo_state.py subcommands that never write state (the Claude twin's
# STATE_READ_VERBS in hooks/ammo-state-validate.sh). Every other subcommand in
# the engine's build_parser counts as a writer, so a new subcommand is a writer
# until it is listed here. tests/test_hook_semantics.py checks this list
# against the engine's own write calls (python ast).
STATE_READ_VERBS=frozenset({'get','validate','next-step'})
_STATE_WRITER_VERBS=None
def state_writer_verbs():
    """The ammo_state.py writer subcommands, read from the engine's build_parser."""
    global _STATE_WRITER_VERBS
    if _STATE_WRITER_VERBS is None:
        try: text=script_path('ammo_state.py').read_text(encoding='utf-8')
        except OSError: text=''
        verbs=set(re.findall(r'add_parser\(\s*["\']([a-z][a-z0-9-]*)["\']',text))
        _STATE_WRITER_VERBS=frozenset(verbs-STATE_READ_VERBS)
    return _STATE_WRITER_VERBS
def invokes_state_writer(command):
    """True when command calls an ammo_state.py writer.

    The verb may follow the script name or a variable that holds it
    (S="<plugin>/scripts/ammo ammo_state.py"; $S set ...), so any whole word
    after the first ammo_state.py counts, as in the Claude twin.
    """
    text=str(command or '')
    if 'ammo_state.py' not in text: return False
    words=set(re.findall(r'[A-Za-z0-9_-]+',text.split('ammo_state.py',1)[1]))
    return bool(words & state_writer_verbs())
def role_from_markers(message,agent_path):
    """The role whose roster doc the message names or whose agent file agent_path is; else ''."""
    agent_file=Path(str(agent_path or '')).name
    for role,(docs,names) in role_markers().items():
        if any(doc in message for doc in docs) or (agent_file and agent_file in names):
            return role
    return ''
def hook_campaign_dir(cwd=None):
    """The layer's campaign: AMMO_ARTIFACT_DIR, else the marker walk up from cwd. None outside."""
    if campaign_paths is None:
        explicit=(os.environ.get('AMMO_ARTIFACT_DIR') or '').strip()
        return Path(os.path.abspath(os.path.expanduser(explicit))) if explicit else None
    try: return campaign_paths.find_campaign_dir(Path(cwd) if cwd else None)
    except Exception: return None
def campaign_for_hook(payload):
    """The campaign of this hook call, or None: then the hook exits 0 with empty stdout."""
    cwd=payload.get('cwd') if isinstance(payload,dict) else None
    return hook_campaign_dir(cwd if isinstance(cwd,str) and cwd else None)
def path_in_campaign(path,campaign):
    """The layer's in_campaign(): realpath containment, the one path classifier."""
    if not campaign: return False
    if campaign_paths is not None: return bool(campaign_paths.in_campaign(path,campaign))
    try:
        inner=os.path.realpath(os.path.expanduser(str(path))); outer=os.path.realpath(os.path.expanduser(str(campaign)))
        return os.path.commonpath([inner,outer]) == outer
    except (OSError,ValueError):
        return False
def _fill_campaign_env(campaign):
    """Fill each unset or empty name the layer derives (explicit env wins).

    AMMO_ARTIFACT_DIR is left alone: active_artifact_dir() treats that name as an
    exact binding (conflicts raise), while the marker is a non-explicit one.
    """
    if campaign_paths is None: return
    try: derived=campaign_paths.campaign_env(campaign)
    except Exception: return
    for key,value in derived.items():
        if key != 'AMMO_ARTIFACT_DIR' and not os.environ.get(key): os.environ[key]=str(value)
def read_stdin_json():
    try: raw=sys.stdin.read(); return json.loads(raw) if raw.strip() else {}
    except Exception: return {}
def emit(o): print(json.dumps(o,sort_keys=True))
def block_pretool(reason): emit({"decision":"block","reason":reason,"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":reason}})
def block_stop(reason): emit({"decision":"block","reason":reason})
def additional_context(event,text): emit({"hookSpecificOutput":{"hookEventName":event,"additionalContext":text}})
def find_repo_root(cwd=None):
    """The engine root of the campaign cwd is in; also fills the campaign env.

    Inside a campaign this is the layer's engine_root() (the dir holding .ammo/),
    and the names the layer derives from target.json are filled into os.environ,
    so AMMO_SESSION_ID readers fall back to the marker id. Outside a campaign:
    the git toplevel of cwd, else cwd itself.
    """
    start=Path(cwd or os.getcwd()).resolve()
    campaign=hook_campaign_dir(start)
    if campaign is not None:
        _fill_campaign_env(campaign)
        if campaign_paths is not None: return Path(campaign_paths.engine_root(campaign)).resolve()
        return Path(campaign).parent.parent.resolve()
    try:
        out=subprocess.check_output(['git','-C',str(start),'rev-parse','--show-toplevel'],stderr=subprocess.DEVNULL,text=True).strip()
        if out: return Path(out).resolve()
    except Exception: pass
    return start
def command_from_payload(payload):
    ti=payload.get('tool_input') or payload.get('toolInput') or {}; return ti.get('command','') if isinstance(ti,dict) else ''
def tool_name_from_payload(payload):
    """Normalize supported native spawn names, including Codex V2 flattening."""
    name = str(payload.get('tool_name') or payload.get('toolName') or '')
    if name in {
        'Agent', 'spawn_agent', 'functions.spawn_agent', 'collaboration.spawn_agent',
        'agents.spawn_agent', 'agentsspawn_agent',
    } or name.endswith('__spawn_agent'):
        return 'spawn_agent'
    return name

def is_child_agent_payload(payload):
    return bool(
        payload.get('agent_id') or payload.get('agentId')
        or payload.get('agent_type') or payload.get('agentType')
        or os.environ.get('CODEX_SUBAGENT') == '1'
    )
def _session_identity_path(server_session_id=None):
    # One file per campaign id: AMMO_GPU_RES_DIR is host-wide (conventions
    # section 5), so one shared file would bind every campaign on the host to
    # the first Codex thread that recorded it.
    root=Path(os.environ.get('AMMO_GPU_RES_DIR') or '/tmp/ammo_gpu_res').expanduser()
    server=server_session_id if server_session_id is not None else trusted_server_session_identity()
    safe=hashlib.sha256(str(server or '').encode('utf-8')).hexdigest()[:24]
    return root/f'codex_hook_session_{safe}.json'
def trusted_server_session_identity(payload=None):
    payload=payload if isinstance(payload,dict) else {}
    for value in (os.environ.get('AMMO_SESSION_ID'),):
        if value: return str(value)
    return ''
def trusted_codex_thread_identity(payload=None):
    payload=payload if isinstance(payload,dict) else {}
    for value in (
        payload.get('session_id'), payload.get('sessionId'), os.environ.get('CODEX_SESSION_ID'),
        payload.get('conversation_id'), payload.get('conversationId'),
    ):
        if value: return str(value)
    return ''
def record_trusted_session_identity(payload):
    server_session_id=trusted_server_session_identity(payload)
    codex_thread_id=trusted_codex_thread_identity(payload)
    if not server_session_id or not codex_thread_id: return ''
    path=_session_identity_path(server_session_id); path.parent.mkdir(parents=True,exist_ok=True)
    existing=load_json(path)
    if isinstance(existing,dict) and (
        existing.get('server_session_id') not in {None,server_session_id}
        or existing.get('codex_thread_id') not in {None,codex_thread_id}
    ):
        raise RuntimeError('Codex hook session identity changed within one server session')
    temp=path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps({
        'server_session_id':server_session_id,'codex_thread_id':codex_thread_id
    },sort_keys=True)+'\n',encoding='utf-8')
    os.replace(temp,path)
    return codex_thread_id
def session_id_from_payload(payload,repo=None):
    trusted=trusted_codex_thread_identity(payload)
    if trusted: return trusted
    for k in ('turn_id','turnId'):
        v=payload.get(k)
        if v: return str(v)
    if repo is not None:
        v=state_session_id(repo,payload)
        if v: return v
    return ''
def prompt_from_payload(payload): return payload.get('prompt') or payload.get('user_prompt') or payload.get('message') or ''
def is_v1_session():
    return os.environ.get('AMMO_CODEX_AGENT_MODE') == 'v1'
def v1_registry():
    # Loaded only for V1. Legacy/V2 hook installations keep their existing
    # dependency behavior, including the classifier's degradation contract.
    scripts=str(_CODEX_SCRIPTS_DIR)
    if scripts not in sys.path: sys.path.insert(0,scripts)
    import codex_agent_registry
    return codex_agent_registry
def v1_agent_binding(payload):
    agent=payload.get('agent_id') or payload.get('agentId')
    if not agent: return None
    registry=v1_registry()
    return registry.binding_for(registry.codex_home(),registry.root_thread_id(payload),agent)
def spawned_agent_identity(payload):
    if is_v1_session() and tool_name_from_payload(payload) == 'spawn_agent':
        return v1_registry().spawned_thread_id(payload) or ''
    return str(payload.get('agent_id') or payload.get('agentId') or '')
def lifecycle_task_name(payload):
    """Resolve an official subagent lifecycle agent_id through Codex's thread index."""
    agent_id=str(payload.get('agent_id') or payload.get('agentId') or '').strip()
    if not agent_id: return ''
    if is_v1_session():
        binding=v1_agent_binding(payload)
        return str(binding.get('task_name') or '') if binding else ''
    home=Path(os.environ.get('CODEX_HOME','~/.codex')).expanduser().resolve()
    db=home/'state_5.sqlite'
    if not db.is_file(): return ''
    try:
        connection=sqlite3.connect(f'{db.as_uri()}?mode=ro',uri=True)
        try: row=connection.execute('SELECT agent_path FROM threads WHERE id = ?',(agent_id,)).fetchone()
        finally: connection.close()
    except sqlite3.Error:
        return ''
    if not row or not isinstance(row[0],str) or not row[0]: return ''
    return row[0].rstrip('/').rsplit('/',1)[-1]
def spawn_role_and_name(payload):
    """Infer an AMMO role and stable Codex task name from a spawn/lifecycle payload."""
    ti=payload.get('tool_input') or payload.get('toolInput') or {}
    if not isinstance(ti,dict): ti={}
    role=normalize_role(ti.get('agent_type') or ti.get('subagent_type') or ti.get('agentType') or payload.get('agent_type') or payload.get('subagent_type'))
    name=str(ti.get('task_name') or ti.get('taskName') or payload.get('task_name') or payload.get('taskName') or ti.get('name') or payload.get('agent_name') or payload.get('agentName') or '').strip()
    event=str(payload.get('hook_event_name') or payload.get('hookEventName') or '')
    if not name and is_v1_session() and tool_name_from_payload(payload) == 'spawn_agent':
        metadata=v1_registry().dispatch_metadata(payload)
        if metadata: name=metadata['task_name']
    if not name and event in {'SubagentStart','SubagentStop'}:
        name=lifecycle_task_name(payload)
    message='\n'.join(str(value) for value in (ti.get('message'),payload.get('message'),payload.get('prompt')) if value)
    agent_path=str(payload.get('agent_path') or payload.get('agentPath') or ti.get('agent_path') or ti.get('agentPath') or '')
    role=role_from_markers(message,agent_path) or role
    lowered=name.lower().replace('-','_')
    if lowered.startswith(('impl_','implementer_','ammo_implementer_')): role='ammo-implementer'
    elif lowered.startswith(('audit_','auditor_','ammo_auditor_')): role='ammo-auditor'
    elif lowered.startswith(('monitor_','ammo_transcript_monitor_')): role='ammo-transcript-monitor'
    return role,name
def normalize_task_name(value):
    """Return one valid multi_agent_v2 task-path segment."""
    normalized=re.sub(r'[^a-z0-9_]+','_',str(value or '').strip().lower())
    return normalized.strip('_')
def task_names_equivalent(left,right):
    normalized_left=normalize_task_name(left)
    return bool(normalized_left) and normalized_left == normalize_task_name(right)
def expected_monitor_name(implementer_name):
    lowered=normalize_task_name(implementer_name)
    for prefix in ('ammo_implementer_','implementer_','impl_'):
        if lowered.startswith(prefix):
            suffix=lowered[len(prefix):]
            return f'monitor_{suffix}' if suffix else ''
    return f'monitor_{lowered}' if lowered else ''
def op_id_from_spawn_payload(payload,implementer_name='',repo=None):
    ti=payload.get('tool_input') or payload.get('toolInput') or {}
    if not isinstance(ti,dict): ti={}
    texts=[ti.get('message'),payload.get('message'),payload.get('prompt')]
    patterns=(
        r'(?im)^\s*OP_ID\s*:\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$',
        r'(?i)\bimplementing optimization\s+([A-Za-z0-9][A-Za-z0-9_.-]*)',
    )
    for text in texts:
        if not isinstance(text,str): continue
        for pattern in patterns:
            match=re.search(pattern,text)
            if match: return match.group(1)
    normalized=normalize_task_name(implementer_name)
    for prefix in ('ammo_implementer_','implementer_','impl_'):
        if normalized.startswith(prefix):
            task_slug=normalized[len(prefix):]
            break
    else:
        task_slug=normalized
    round_match=re.fullmatch(r'r([1-9][0-9]*)_(.+)',task_slug)
    round_hint=int(round_match.group(1)) if round_match else None
    op_slug=round_match.group(2) if round_match else task_slug
    if repo is not None and op_slug:
        artifact=active_artifact_dir(repo,payload)
        state=load_json(artifact/'state.json') if artifact else None
        campaign=state.get('campaign') if isinstance(state,dict) else None
        rounds=campaign.get('rounds') if isinstance(campaign,dict) else None
        candidates=[]
        round_records=rounds if isinstance(rounds,list) else []
        if round_hint is not None:
            round_records=round_records[round_hint-1:round_hint]
        for round_record in reversed(round_records):
            if not isinstance(round_record,dict): continue
            parallel=round_record.get('parallel_tracks')
            tracks=parallel.get('tracks') if isinstance(parallel,dict) else None
            if isinstance(tracks,dict): candidates.extend(str(value) for value in tracks)
            debate=round_record.get('debate')
            selected=debate.get('selected_candidates') if isinstance(debate,dict) else None
            if isinstance(selected,dict): selected=selected.values()
            if isinstance(selected,(list,tuple)):
                for value in selected:
                    if isinstance(value,dict) and value.get('op_id'): candidates.append(str(value['op_id']))
                    elif isinstance(value,str): candidates.append(value)
        matches=[]
        for candidate in candidates:
            if normalize_task_name(candidate) == op_slug and candidate not in matches: matches.append(candidate)
        if len(matches) == 1: return matches[0]
        event=str(payload.get('hook_event_name') or payload.get('hookEventName') or '')
        if event == 'SubagentStart': return ''
    return task_slug
def _monitor_pair_path(repo,payload):
    sid=session_id_from_payload(payload,repo)
    if not sid: return None
    safe=hashlib.sha256(str(sid).encode('utf-8')).hexdigest()[:24]
    root=Path(os.environ.get('AMMO_GPU_RES_DIR') or '/tmp/ammo_gpu_res').expanduser()
    return root/f'codex_monitor_pairs_{safe}.json'
def pending_monitor_pairs(repo,payload):
    path=_monitor_pair_path(repo,payload)
    doc=load_json(path) if path else None
    records=doc.get('pending') if isinstance(doc,dict) else None
    return [record for record in records if isinstance(record,dict)] if isinstance(records,list) else []
def _write_monitor_pairs(path,records):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps({'pending':records},sort_keys=True,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,path)
def record_monitor_pair(repo,payload,implementer_name):
    path=_monitor_pair_path(repo,payload)
    expected=expected_monitor_name(implementer_name)
    if path is None or not expected: return False
    records=pending_monitor_pairs(repo,payload)
    agent_id=spawned_agent_identity(payload)
    existing=next((record for record in records if task_names_equivalent(record.get('implementer_name'),implementer_name)),None)
    candidate_op_id=op_id_from_spawn_payload(payload,implementer_name,repo)
    if existing is None:
        if not candidate_op_id: return False
        records.append({'implementer_name':normalize_task_name(implementer_name),'implementer_agent_id':agent_id,'op_id':candidate_op_id,'expected_monitor_name':expected,'status':'pending'})
    else:
        if agent_id: existing['implementer_agent_id']=agent_id
        if candidate_op_id and candidate_op_id != existing.get('op_id'): existing['op_id']=candidate_op_id
        if not existing.get('op_id'): return False
        existing['expected_monitor_name']=expected
    _write_monitor_pairs(path,records)
    return True
def clear_monitor_pair(repo,payload,monitor_name):
    path=_monitor_pair_path(repo,payload)
    if path is None: return False
    records=pending_monitor_pairs(repo,payload)
    remaining=[record for record in records if not task_names_equivalent(record.get('expected_monitor_name'),monitor_name)]
    if len(remaining)==len(records): return False
    if remaining: _write_monitor_pairs(path,remaining)
    else:
        try: path.unlink()
        except FileNotFoundError: pass
    return True
def start_monitor_pair(repo,payload,monitor_name):
    path=_monitor_pair_path(repo,payload)
    if path is None: return False
    records=pending_monitor_pairs(repo,payload); changed=False
    for record in records:
        if task_names_equivalent(record.get('expected_monitor_name'),monitor_name):
            record['status']='active'
            record['monitor_agent_id']=spawned_agent_identity(payload)
            changed=True
    if changed: _write_monitor_pairs(path,records)
    return changed
def complete_monitor_pair(repo,payload,monitor_name,artifact_dir):
    path=_monitor_pair_path(repo,payload)
    if path is None: return False
    records=pending_monitor_pairs(repo,payload); target=None
    for record in records:
        if task_names_equivalent(record.get('expected_monitor_name'),monitor_name) and record.get('status') == 'active':
            target=record; break
    if target is None: return False
    op_id=str(target.get('op_id') or '').strip()
    if not op_id:
        normalized_monitor=normalize_task_name(monitor_name)
        op_id=normalized_monitor[len('monitor_'):] if normalized_monitor.startswith('monitor_') else ''
    state=load_json(Path(artifact_dir)/'state.json')
    campaign=state.get('campaign') if isinstance(state,dict) else None
    round_id=campaign.get('current_round') if isinstance(campaign,dict) else None
    audit_dir=Path(artifact_dir)/'rounds'/str(round_id)/'tracks'/op_id/'monitor_audits'
    logs=[candidate for candidate in audit_dir.glob('*_observations.md') if candidate.is_file() and candidate.stat().st_size > 0] if audit_dir.is_dir() else []
    queue=Path(artifact_dir)/'monitor_interventions.jsonl'
    final=None
    for entry in read_monitor_jsonl(queue):
        record=entry.get('record')
        if not isinstance(record,dict): continue
        if (normalize_role(record.get('emitter')) == 'ammo-transcript-monitor'
                and str(record.get('severity') or '').upper() == 'INFO'
                and str(record.get('target_rollout_id') or '') == str(target.get('implementer_agent_id') or '')
                and 'poll' in str(record.get('summary') or '').lower()):
            final=record
    if not logs or final is None: return False
    chosen=max(logs,key=lambda candidate:candidate.stat().st_mtime_ns)
    target['status']='satisfied'; target['summary_path']=str(chosen); target['summary_sha256']=hashlib.sha256(chosen.read_bytes()).hexdigest(); target['final_summary']=final.get('summary')
    _write_monitor_pairs(path,records)
    return True
class CampaignResolutionError(ValueError):
    """Campaign identity is unsafe, conflicting, or ambiguous."""


class CampaignStateError(CampaignResolutionError):
    """The exact state is unreadable; inspection must remain possible."""


def _safe_campaign_state(directory):
    candidate = Path(os.path.abspath(directory)) / 'state.json'
    if any(path.is_symlink() for path in (candidate, *candidate.parents)):
        raise CampaignResolutionError(f'unsafe symlink campaign state: {candidate}')
    if not candidate.exists():
        return None
    state = load_json(candidate)
    if not isinstance(state, dict):
        raise CampaignStateError(f'unreadable campaign state: {candidate}')
    return candidate.parent, state


def _campaign_identity_matches(state, payload, explicit=False, server=None):
    server = trusted_server_session_identity(payload) if server is None else server
    thread = trusted_codex_thread_identity(payload)
    state_server = str(state.get('session_id') or state.get('sessionId') or '')
    state_thread = str(state.get('codex_thread_id') or '')
    if server and state_server and server != state_server:
        return False
    if thread and state_thread and thread != state_thread:
        return False
    if explicit:
        return True
    return bool(
        (server and state_server == server)
        or (thread and state_thread == thread)
        or (not server and not state_thread and thread and state_server == thread)
    )


def active_artifact_dir(repo, payload=None):
    """Resolve an exact binding or the marker's campaign; never use recency.

    AMMO_ARTIFACT_DIR is an exact binding: it wins, and a conflicting trusted
    identity raises. Otherwise the layer walks up from repo to the
    <engine>/.ammo/active marker. The marker names one campaign dir, so a copy
    of state.json in rounds/{N}/_archive/ or below the campaign root is never a
    second campaign (session 858af077: one such copy made every hook raise
    "multiple campaigns match"). The marker campaign binds only on a positive
    identity match, with the campaign id standing in for an unset
    AMMO_SESSION_ID (conventions section 3). An unreadable or mismatched marker
    state resolves to None, as the server's discovery did, so a damaged state
    never locks the session out of the command that repairs it.
    """
    payload = payload if isinstance(payload, dict) else {}
    explicit = os.environ.get('AMMO_ARTIFACT_DIR')
    if explicit:
        raw = Path(explicit).expanduser()
        record = _safe_campaign_state(raw if raw.is_absolute() else repo / raw)
        # A new target may not have state yet. Never fall back to another target.
        if record is None:
            return None
        directory, state = record
        if not _campaign_identity_matches(state, payload, explicit=True):
            raise CampaignResolutionError('AMMO_ARTIFACT_DIR conflicts with trusted session/root identity')
        return directory

    if campaign_paths is None:
        return None
    try:
        campaign = campaign_paths.find_campaign_dir(Path(repo), env={})
    except Exception:
        return None
    if campaign is None:
        return None
    try:
        record = _safe_campaign_state(campaign)
    except CampaignResolutionError:
        return None
    if record is None:
        return None
    directory, state = record
    server = trusted_server_session_identity(payload) or campaign_paths.campaign_id(campaign)
    if isinstance(state.get('campaign'), dict) and _campaign_identity_matches(state, payload, server=server):
        return directory
    return None
def load_json(p):
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return None
def state_session_id(repo,payload=None):
    ad=active_artifact_dir(repo,payload)
    st=load_json(ad/'state.json') if ad else None
    if isinstance(st,dict):
        for k in ('codex_thread_id','session_id','sessionId'):
            v=st.get(k)
            if v: return str(v)
    return ''
def is_ammo_context(cmd,repo,payload=None): return (str(_PLUGIN_ROOT) in cmd or CAMPAIGN_SEGMENT+'/' in cmd or active_artifact_dir(repo,payload) is not None)
# Shell-command classification lives in ONE place: the ammo skill's
# hook_cmd_classify.py, which the shell hooks already call. Import it here so
# the Codex hooks and the shell hooks cannot drift apart, and re-export the
# names this module's other functions (and the sibling guards) call.
#
# The import AND every attribute read below are guarded because EVERY Codex hook
# imports this module. An unguarded ModuleNotFoundError -- or an unguarded
# AttributeError from a classifier that imports but is incomplete, because a
# zero-byte or truncated file still parses -- would kill all five hooks with a
# traceback and an empty stdout, so a missing or partial classifier would
# silently disable the whole enforcement layer -- every PreToolUse deny and the
# Stop gate at once. That is reachable: the plugin ships codex/hooks/ and
# scripts/ as one tree, and a partial copy or restore of the plugin can land one
# without the other, or truncate one of them. One missing attribute degrades all
# five re-exports together, so the fail direction below is the only degraded
# behavior either way.
#
# Fail direction on failure, matching the shell hooks that guard the same file:
#   is_static_inspection_command -> False   fail-CLOSED, as ammo-pretool-guard.sh
#       does. Nothing earns the inspection fast path, so every caller falls
#       through to its normal guard logic instead of exiting early.
#   split_shell_command -> inline shlex.split. It carries no policy, so the
#       real semantics are reproduced rather than degraded.
#   the private segment helpers -> empty/False, which also degrades
#       command_invokes_accelerator_heavy_tool to False. That predicate feeds a
#       hard deny in pre_tool_use_guard.py for CUDA and Neuron execution tools
#       without a reservation -- so that one guard is fail-OPEN when degraded:
#       its deny becomes an allow. The stderr warning below discloses it.
_CLASSIFY_SCRIPTS_DIR=str(_SCRIPTS_DIR)
if _CLASSIFY_SCRIPTS_DIR not in sys.path: sys.path.insert(0,_CLASSIFY_SCRIPTS_DIR)
try:
    import hook_cmd_classify as _cmd_classify
    # Inside the guard on purpose: an incomplete classifier fails here, not at
    # import, and both failures must degrade instead of raising.
    split_shell_command=_cmd_classify.split_shell_command
    _executable_segments=_cmd_classify._executable_segments
    _is_python_executable_name=_cmd_classify._is_python_executable_name
    _is_read_only_python_segment=_cmd_classify._is_read_only_python_segment
    is_static_inspection_command=_cmd_classify.is_static_inspection_command
    _shared_accelerator_heavy=_cmd_classify.command_invokes_accelerator_heavy_tool
except Exception as _classify_exc:
    _CLASSIFY_DEGRADED=str(_classify_exc) or _classify_exc.__class__.__name__
    # Loud on stderr, never on stdout: stdout carries the hook verdict document
    # and must stay parseable. A degraded layer must not also be a silent one.
    try:
        sys.stderr.write(
            'AMMO hooks DEGRADED: '+_CLASSIFY_SCRIPTS_DIR+'/hook_cmd_classify.py '
            'unavailable or incomplete ('+_CLASSIFY_DEGRADED+'). Inspection fast '
            'paths are OFF (fail-closed); GPU-heavy tool detection is OFF, so the '
            'unreserved-GPU deny in pre_tool_use_guard.py allows those commands '
            '(fail-open).\n'
        )
    except Exception:
        pass
    import shlex as _shlex
    def split_shell_command(command):
        try: return _shlex.split(command or '')
        except ValueError: return []
    def _executable_segments(parts): return []
    def _is_python_executable_name(name): return False
    def _is_read_only_python_segment(tail): return False
    def is_static_inspection_command(command): return False
    def _shared_accelerator_heavy(command): return False
else:
    _CLASSIFY_DEGRADED=''
def command_invokes_vllm_bench_latency(command):
    parts=split_shell_command(command)
    names=[Path(part).name for part in parts]
    for idx,name in enumerate(names[:-2]):
        if name == 'vllm' and parts[idx+1:idx+3] == ['bench','latency']:
            return True
        if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?',name) and idx + 4 <= len(parts):
            if parts[idx+1:idx+5] == ['-m','vllm','bench','latency']:
                return True
    return False
def command_invokes_accelerator_heavy_tool(command):
    return _shared_accelerator_heavy(command)
def command_invokes_gpu_heavy_tool(command):
    """Compatibility alias retained for existing hooks and tests."""
    return command_invokes_accelerator_heavy_tool(command)
def monitor_queue_paths(repo,payload=None):
    paths=[]; env=os.environ.get('AMMO_MONITOR_QUEUE')
    if env: paths.append(Path(env).expanduser())
    ad=active_artifact_dir(repo,payload)
    if ad:
        paths.append(ad/'monitor_interventions.jsonl')
        try:
            paths.extend(sorted(ad.glob('**/monitor_interventions.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True))
        except Exception:
            pass
    seen=[]
    for p in paths:
        p=(repo/p).resolve() if not p.is_absolute() else p.resolve()
        if p not in seen: seen.append(p)
    return seen
def read_monitor_jsonl(path):
    entries=[]
    try:
        for line_no,line in enumerate(path.read_text(encoding='utf-8').splitlines(),start=1):
            if not line.strip():
                entries.append({'line_no':line_no,'raw':line,'record':None})
                continue
            try: record=json.loads(line)
            except Exception: record=None
            entries.append({'line_no':line_no,'raw':line,'record':record})
    except FileNotFoundError: pass
    except Exception: pass
    return entries
def record_session_id(record):
    return str(record.get('target_session_id') or record.get('session_id') or record.get('targetSessionId') or '')
def _record_session(record):
    return record_session_id(record)
def _dedupe_nonempty(values):
    seen=[]
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
def monitor_target_session_ids(repo,payload):
    state_sid=state_session_id(repo,payload)
    if state_sid:
        return _dedupe_nonempty([state_sid])
    return _dedupe_nonempty([session_id_from_payload(payload,repo)])
def stable_monitor_record_id(path,index,record):
    explicit=record.get('record_id') or record.get('id')
    if explicit: return str(explicit)
    body=json.dumps(record,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(f'{path}:{index}:{body}'.encode('utf-8')).hexdigest()[:12]
def _record_open(record):
    status=str(record.get('status') or '').lower()
    if status in {'acknowledged','resolved','dismissed','closed'}: return False
    return not (record.get('acknowledged_at') or record.get('resolved_at'))
def _record_requires_ack(record):
    if record.get('ack_required') is True: return True
    if isinstance(record.get('ack_required'),str): return record.get('ack_required').lower() in {'1','true','yes'}
    return False
def _record_expired(record):
    expires=str(record.get('expires_at') or '').strip()
    if not expires: return False
    try:
        dt=datetime.fromisoformat(expires.replace('Z','+00:00'))
        return dt <= datetime.now(timezone.utc)
    except Exception:
        return False
def pending_monitor_records(repo,payload,severities=None):
    sids=set(monitor_target_session_ids(repo,payload))
    if not sids: return []
    wanted={s.upper() for s in severities} if severities else None
    records=[]
    for path in monitor_queue_paths(repo,payload):
        for entry in read_monitor_jsonl(path):
            rec=entry.get('record')
            if not isinstance(rec,dict): continue
            idx=entry.get('line_no') or 0
            if _record_session(rec) not in sids: continue
            if not _record_open(rec): continue
            if _record_expired(rec): continue
            sev=str(rec.get('severity') or '').upper()
            if wanted and sev not in wanted: continue
            rid=stable_monitor_record_id(path,idx,rec)
            rec=dict(rec); rec['_queue_path']=str(path); rec['_record_id']=rid; records.append(rec)
    return records
def blocking_monitor_records(repo,payload):
    return [r for r in pending_monitor_records(repo,payload,{'CRITICAL','HARD_GATE'}) if _record_requires_ack(r)]
def format_monitor_records(records,limit=3):
    lines=[]
    for rec in records[:limit]:
        sev=str(rec.get('severity') or 'INFO').upper()
        summary=str(rec.get('summary') or '').strip()
        evidence=str(rec.get('evidence') or '').strip()
        action=str(rec.get('recommended_action') or '').strip()
        rid=str(rec.get('_record_id') or rec.get('record_id') or rec.get('id') or '').strip()
        prefix=f"AMMO-MONITOR: [{sev}]"
        if rid: prefix+=f" id={rid}"
        lines.append(f"{prefix} {summary}")
        if evidence: lines.append(f"Evidence: {evidence}")
        if action: lines.append(f"Recommended action: {action}")
    if len(records)>limit: lines.append(f"... {len(records)-limit} more pending monitor item(s).")
    return '\n'.join(lines)
