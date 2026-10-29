#!/usr/bin/env python3
import glob,os,re,stat
import shlex
import sys
from pathlib import Path
from common import CampaignResolutionError, campaign_for_hook, codex_script_path, plugin_path, script_path
from common import is_child_agent_payload, is_v1_bound_role, is_v1_session, normalize_role, tool_name_from_payload, v1_agent_binding, v1_registry
from common import active_artifact_dir,additional_context,block_pretool,blocking_monitor_records,command_from_payload,command_invokes_gpu_heavy_tool,command_invokes_vllm_bench_latency,find_repo_root,format_monitor_records,is_ammo_context,is_static_inspection_command,load_json,pending_monitor_pairs,read_stdin_json,record_session_id,session_id_from_payload,spawn_role_and_name,state_session_id,task_names_equivalent

# Outside a campaign every Codex hook exits 0 with empty stdout (conventions
# section 7), so the guards below run only for AMMO campaign work. The
# package-install deny and the sweep-script .venv guard were dropped with their
# Claude twins (ammo-pip-guard.sh, ammo-venv-python-guard.sh; section 7 drop
# list): the two runtimes keep denying the same commands, and the engine python
# is whatever target.json engine.venv records, not a fixed .venv path.
p=read_stdin_json()
if campaign_for_hook(p) is None: raise SystemExit(0)
cmd=command_from_payload(p); repo=find_repo_root(p.get('cwd'))
def _guard_v1_identity():
    if not is_v1_session(): return
    role=normalize_role(p.get('agent_type') or p.get('agentType'))
    ti=p.get('tool_input') or p.get('toolInput') or {}
    spawning=tool_name_from_payload(p) == 'spawn_agent'
    spawn_role=normalize_role(ti.get('agent_type')) if isinstance(ti,dict) else ''
    try:
        if is_child_agent_payload(p) and is_v1_bound_role(role) and v1_agent_binding(p) is None:
            if cmd and is_static_inspection_command(cmd): return
            block_pretool('AMMO task registration is pending. Read your role and wait for the parent spawn result before campaign work.')
            raise SystemExit(0)
        if spawning and is_v1_bound_role(spawn_role):
            registry=v1_registry()
            binding=registry.validate_dispatch(registry.codex_home(),p)
            artifact=active_artifact_dir(repo,p)
            if artifact is None or Path(binding['artifact_dir']) != artifact:
                raise ValueError('The AMMO dispatch must use the exact active campaign.')
    except (ValueError, OSError, ImportError) as exc:
        block_pretool(f'AMMO V1 agent binding must be resolved before campaign work: {exc}')
        raise SystemExit(0)
_guard_v1_identity()
try:
    active_artifact_dir(repo, p)
except CampaignResolutionError as exc:
    if not is_static_inspection_command(cmd):
        block_pretool(f'AMMO campaign binding must be resolved before mutation: {exc}')
    raise SystemExit(0)
def _guard_session_identity_mutation():
    if os.environ.get('AMMO_ALLOW_ANCHOR_MUTATION') == '1': return
    haystack='\n'.join(_payload_texts())
    if any(name in haystack for name in ('codex_hook_session','ammo-agent-registry.json','ammo-agent-registry.lock')) or re.search(
        r'\b(?:rm|unlink|rmdir|shutil\.rmtree)\b[^\n]*(?:AMMO_GPU_RES_DIR|/tmp/ammo_gpu_res)',
        haystack,
    ):
        block_pretool(
            'AMMO trusted-session identity and the GPU reservation dir are owned by the hooks. '
            'Set AMMO_ALLOW_ANCHOR_MUTATION=1 only for validated recovery.'
        )
        raise SystemExit(0)
def _tool_input():
    ti=p.get('tool_input') or p.get('toolInput') or {}
    return ti if isinstance(ti,dict) else {}
def _payload_texts():
    ti=_tool_input()
    texts=[cmd,str(p.get('command') or ''),str(p.get('haystack') or '')]
    for key in ('command','haystack','file_path','filePath','path','content','new_string','newString','old_string','oldString','patch'):
        if key in ti: texts.append(str(ti.get(key) or ''))
    return [t for t in texts if t]
def _vllm_op_default_on(text):
    patterns=[
        r'\bVLLM_OP\d+\b[^\n#]*(?:=\s*(?:True|1\b|["\']1["\'])|:\s*bool\s*=\s*True|,\s*True\b)',
        r'\bVLLM_OP\d+\b[^\n#]*(?:default\s*=\s*(?:True|1|["\']1["\']))',
        r'default\s*=\s*(?:True|1|["\']1["\'])[^\n#]*\bVLLM_OP\d+\b',
        r'os\.environ\.get\(\s*["\']VLLM_OP\d+["\']\s*,\s*["\']1["\']\s*\)',
    ]
    return any(re.search(pattern,text,re.IGNORECASE) for pattern in patterns)
def _env_default_guard_paths():
    ti=_tool_input()
    paths=[]
    for key in ('file_path','filePath','path','target_file','targetFile'):
        value=ti.get(key)
        if isinstance(value,str) and value:
            try: paths.append(Path(value.strip().strip('"\'')).name)
            except Exception: pass
    return paths
def _current_is_child_agent():
    if is_child_agent_payload(p): return True
    if os.environ.get('CODEX_SUBAGENT') == '1' or os.environ.get('CLAUDE_SUBAGENT') == '1':
        return True
    if p.get('agent_type') or p.get('agentType'):
        return True
    agent_name=str(p.get('agentName') or p.get('agent_name') or '')
    return bool(agent_name and agent_name != 'team-lead')
def _resolve_payload_path(raw):
    try:
        path=Path(str(raw).strip().strip('"\''))
        if not str(path):
            return None
        if not path.is_absolute():
            path=(Path(p.get('cwd') or os.getcwd())/path)
        return path.resolve()
    except Exception:
        return None
def _edit_target_paths():
    ti=_tool_input()
    paths=[]
    for key in ('file_path','filePath','path','target_file','targetFile','notebook_path','notebookPath'):
        value=ti.get(key)
        if isinstance(value,str) and value:
            resolved=_resolve_payload_path(value)
            if resolved is not None:
                paths.append(resolved)
    for text in _payload_texts():
        for match in re.findall(r'^\*\*\* (?:Add|Update|Delete) File:\s+(.+)$',text,flags=re.MULTILINE):
            resolved=_resolve_payload_path(match)
            if resolved is not None:
                paths.append(resolved)
    seen=[]
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen
def _guard_orchestrator_owned_ammo_files():
    if not _current_is_child_agent():
        return
    # The plugin's own references/, orchestration/, agents/ and codex/agents/
    # dirs (one prefix each, never a bare dir name, which would also match the
    # engine's own references/ or agents/ dirs), plus installed Codex agent
    # definitions in any .codex/agents/ dir.
    protected=tuple(
        os.path.realpath(str(plugin_path(*parts))).rstrip('/')+'/'
        for parts in (('references',),('orchestration',),('agents',),('codex','agents'))
    )
    for path in _edit_target_paths():
        posix='/' + path.as_posix().lstrip('/')
        real=os.path.realpath(str(path))
        if real.startswith(protected) or '/.codex/agents/' in posix:
            block_pretool(
                'Subagents may not edit '+str(path)+'. Files under the AMMO plugin\'s '
                'references/, orchestration/, agents/ and codex/agents/ ('+str(plugin_path())+'), '
                'and under .codex/agents/, are orchestrator-owned. '
                'Surface the proposed change to the lead instead.'
            )
            raise SystemExit(0)
def _guard_vllm_op_defaults():
    haystack='\n'.join(_payload_texts())
    if 'envs.py' not in _env_default_guard_paths() or 'VLLM_OP' not in haystack: return
    if _vllm_op_default_on(haystack):
        block_pretool('VLLM_OP feature flags in envs.py must default off. Use default=False/0 and require explicit opt-in before enabling optimized paths.')
        raise SystemExit(0)
# PUMICE_* engine-env guard (twin of ammo-env-default-guard.sh). A campaign flag
# on the Pumice runner is PUMICE_<SCOPE>_<MECHANISM>[_<ARCH>], read with
# os.environ.get (or env::var), default off. On a .py or .rs edit, block a
# PUMICE_* name that is not on the allowlist when its read does not default off,
# and block a default change to an allowlisted name. The default-off forms are
# listed in the Claude twin's header; the unit is a line for Python and a
# statement (lines joined up to ";") for Rust.
# The allowlist is the one the Claude twin reads: <plugin>/hooks/pumice_env_allowlist.txt.
_PUMICE_ACCESSOR_RE=re.compile(r'getenv|environ|env::var|env::var_os|std::env|env!\s*\(')
_PUMICE_OFF_PY=r'(?:None|False|0|["\']0["\']|["\']["\']|["\'](?:false|False)["\'])'
def _pumice_new_read_off(unit,name,is_rust):
    if is_rust:
        if re.search(r'!=|(?:^|[^\w])!',unit): return False
        rest=re.sub(r'unwrap_or_default\s*\(\s*\)','',unit)
        rest=re.sub(r'unwrap_or\s*\(\s*(?:false|0|"0"|"")\s*\)','',rest)
        rest=re.sub(r'map_or\s*\(\s*false\s*,','',rest)
        return not re.search(r'unwrap_or|map_or|(?:^|[^\w])(?:match|else|Err)(?:[^\w]|$)',rest)
    if re.search(r'!=|(?:^|[^\w])(?:not|or)(?:[^\w]|$)',unit): return False
    m=re.search(r'["\']'+re.escape(name)+r'["\']',unit)
    after=unit[m.end():] if m else ''
    if re.match(r'\s*\]\s*=(?:[^=]|$)',after): return False
    if re.match(r'\s*[\])]',after): return True
    if re.match(r'\s+in\s+os\.environ',after): return True
    return bool(re.match(r'\s*,\s*(?:default\s*=\s*)?'+_PUMICE_OFF_PY+r'\s*[),]',after))
def _pumice_allowlist_names():
    names=set()
    try:
        for raw in plugin_path('hooks','pumice_env_allowlist.txt').read_text().splitlines():
            line=raw.strip()
            if not line or line.startswith('#'): continue
            if line.startswith(('PUMICE_','VLLM_')): names.add(line)
    except OSError:
        pass
    return names
def _pumice_new_content():
    ti=_tool_input()
    texts=[]
    for key in ('content','new_string','newString'):
        value=ti.get(key)
        if isinstance(value,str) and value: texts.append(value)
    return '\n'.join(texts)
def _pumice_env_offenders(text,allow,is_rust=False):
    new_names=[]; default_names=[]
    lines=[re.sub(r'(^|\s)#.*$','',raw) for raw in text.splitlines()]
    if is_rust:
        units=' '.join(re.sub(r'//.*$','',line) for line in lines).split(';')
    else:
        units=lines
    for line in units:
        if not line.strip() or not _PUMICE_ACCESSOR_RE.search(line): continue
        for match in re.finditer(r'["\'](PUMICE_[A-Z0-9_]{3,})["\']',line):
            name=match.group(1)
            if name in allow:
                if re.search(r'["\']'+re.escape(name)+r'["\']\s*,',line) or re.search(r'unwrap_or(_else|_default)?\s*\(',line):
                    if name not in default_names: default_names.append(name)
            elif name not in new_names and not _pumice_new_read_off(line,name,is_rust):
                new_names.append(name)
    return new_names,default_names
def _guard_pumice_env():
    paths=_env_default_guard_paths()
    if not any(pth.endswith(('.py','.rs')) for pth in paths): return
    text=_pumice_new_content()
    if 'PUMICE_' not in text: return
    is_rust=any(pth.endswith('.rs') for pth in paths)
    new_names,default_names=_pumice_env_offenders(text,_pumice_allowlist_names(),is_rust)
    if not new_names and not default_names: return
    parts=[]
    if new_names: parts.append('new env(s) read with a default that is not off: '+', '.join(new_names))
    if default_names: parts.append('default change to allowlisted env(s): '+', '.join(default_names))
    block_pretool('BLOCKED: PUMICE_* engine env change ('+'; '.join(parts)+'). A campaign flag is PUMICE_<SCOPE>_<MECHANISM>[_<ARCH>], read with os.environ.get (or env::var), default off, and enabled per arm through opt_env in target.json. Default a new flag off at the read, for example os.environ.get("PUMICE_MOE_TWO_STREAM_SM90", "0") == "1", and do not negate or replace a missing value. Pumice owns the defaults of the names it already reads: set those per arm via opt_env instead.')
    raise SystemExit(0)
def _worktree_root(cwd):
    if not cwd: return None
    try: start=Path(cwd).resolve()
    except Exception: return None
    for path in [start,*start.parents]:
        if path.parent.name == 'worktrees' and path.parent.parent.name == '.codex':
            return path
    return None
def _split_command(command):
    try: return shlex.split(command)
    except ValueError: return []
def _invoked_pythonish_tools(command):
    tools=[]
    for token in _split_command(command):
        if token in {'&&',';','||','|','(',')'} or '=' in token and not token.startswith(('/','.')):
            continue
        name=Path(token).name
        if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?|pytest|pip(?:\d+)?|uv',name):
            tools.append(token)
    return tools
def _uses_worktree_venv(command,worktree):
    activate=str(worktree/'.venv'/'bin'/'activate')
    if re.search(r'(?:^|[;&|]\s*)(?:source|\.)\s+(?:"|\')?'+re.escape(activate)+r'(?:"|\')?',command):
        return True
    if re.search(r'(?:^|[;&|]\s*)(?:source|\.)\s+(?:"|\')?(?:\./)?\.venv/bin/activate(?:"|\')?',command):
        return True
    parts=_split_command(command)
    for idx,token in enumerate(parts[:-1]):
        if token in {'source','.'}:
            try:
                sourced=Path(parts[idx+1]).expanduser()
                if not sourced.is_absolute():
                    sourced=(worktree/sourced)
                if sourced.resolve() == (worktree/'.venv'/'bin'/'activate').resolve():
                    return True
            except Exception:
                pass
    venv_bin=(worktree/'.venv'/'bin').resolve()
    for token in _invoked_pythonish_tools(command):
        try:
            path=Path(token).expanduser()
            if path.is_absolute() and path.resolve().parent == venv_bin:
                return True
        except Exception:
            pass
    return False
def _read_only_pythonish(command):
    parts=_split_command(command)
    if not parts: return False
    meaningful=[x for x in parts if x not in {'env','command'} and not (('=' in x and not x.startswith(('/','.'))))]
    if not meaningful: return False
    joined=' '.join(meaningful)
    first=Path(meaningful[0]).name
    if first.startswith('python'):
        return bool(re.fullmatch(r'python(?:\d+(?:\.\d+)?)?\s+(-V|--version|-VV|--help|-h)',joined))
    if re.fullmatch(r'pip(?:\d+)?',first):
        return bool(re.fullmatch(r'pip(?:\d+)?\s+(--version|-V|list|freeze|show)(?:\s+[\w_.-]+)?',joined))
    if first == 'uv':
        return bool(re.fullmatch(r'uv\s+(--version|-V|help|pip\s+(list|freeze|show)(?:\s+[\w_.-]+)?)',joined))
    if first == 'pytest':
        return bool(re.fullmatch(r'pytest\s+(--version|-h|--help|--collect-only)(?:\s+[\w./:-]+)*',joined))
    return False
def _venv_sentinel():
    root=Path(os.environ.get('AMMO_GPU_RES_DIR') or '/tmp/ammo_gpu_res').expanduser()
    sid=session_id_from_payload(p,repo) or p.get('turn_id') or p.get('turnId') or 'session'
    safe=re.sub(r'[^A-Za-z0-9_.-]+','_',str(sid))[:96] or 'session'
    return root/f'worktree_venv_guard_{safe}.seen'
def _guard_worktree_venv():
    if not cmd: return
    worktree=_worktree_root(p.get('cwd'))
    if not worktree: return
    if not _invoked_pythonish_tools(cmd): return
    if _uses_worktree_venv(cmd,worktree): return
    detail=' Source '+str(worktree/'.venv'/'bin'/'activate')+' or invoke absolute executables under '+str(worktree/'.venv'/'bin')+'.'
    block_pretool('Python, pytest, pip, and uv commands inside .codex/worktrees must use the worktree virtualenv.'+detail)
    raise SystemExit(0)
_guard_session_identity_mutation()
_guard_vllm_op_defaults()
_guard_pumice_env()
_guard_orchestrator_owned_ammo_files()
_guard_worktree_venv()
def _guard_monitor_pairing():
    pending_pairs=[record for record in pending_monitor_pairs(repo,p) if record.get('status') == 'pending']
    if not pending_pairs: return
    role,name=spawn_role_and_name(p)
    expected={str(record.get('expected_monitor_name') or '') for record in pending_pairs}
    if role == 'ammo-transcript-monitor' and any(task_names_equivalent(name,value) for value in expected):
        return
    if cmd and is_static_inspection_command(cmd):
        return
    if cmd and any(script in cmd for script in ('resolve_codex_transcript.py','codex_agent_registry.py')) and not re.search(r'[;&|`$<>]|\n',cmd):
        return
    owed=', '.join(sorted(value for value in expected if value)) or '<matching monitor>'
    block_pretool(
        'AMMO implementer spawn is awaiting its mandatory paired transcript monitor. '
        'Spawn exactly '+owed+' before any further intercepted mutation.'
    )
    raise SystemExit(0)
_guard_monitor_pairing()
def _monitor_ack_shell_safe(command):
    """Allow literal punctuation in quotes, never executable shell syntax."""
    quote=None
    escaped=False
    for char in command:
        if char in '\r\n': return False
        if quote == "'":
            if char == "'": quote=None
            continue
        if escaped:
            if quote is None and char in ';&|<>$`': return False
            escaped=False
            continue
        if char == '\\':
            escaped=True
            continue
        if quote == '"':
            if char == '"': quote=None
            elif char in '$`': return False
            continue
        if char == "'" or char == '"':
            quote=char
        elif char in ';&|<>$`*?[]{}~':
            return False
    return quote is None and not escaped
def _monitor_ack_file_inspection(command):
    """Permit only non-interpreting readers for this security-sensitive script."""
    if not _monitor_ack_shell_safe(command): return False
    try: parts=shlex.split(command)
    except ValueError: return False
    trusted_readers={
        '/bin/cat','/usr/bin/cat','/bin/head','/usr/bin/head','/bin/tail','/usr/bin/tail',
        '/bin/wc','/usr/bin/wc','/bin/stat','/usr/bin/stat','/bin/sha256sum','/usr/bin/sha256sum',
        '/bin/md5sum','/usr/bin/md5sum','/bin/cmp','/usr/bin/cmp',
    }
    if not parts or parts[0] not in trusted_readers: return False
    return any(Path(token).name == 'monitor_queue_ack.py' for token in parts[1:])
def _monitor_ack_script_mentioned(command,depth=0):
    if 'monitor_queue_ack.py' in command: return True
    if depth > 2: return False
    try: parts=shlex.split(command)
    except ValueError: return False
    canonical=codex_script_path('monitor_queue_ack.py')
    for token in parts:
        if Path(token).name == 'monitor_queue_ack.py': return True
        try:
            candidate=_resolve_monitor_ack_path(token)
            if candidate.exists() and canonical.exists() and os.path.samefile(candidate,canonical): return True
        except (OSError,RuntimeError):
            pass
        if glob.has_magic(token):
            try:
                pattern=token if Path(token).is_absolute() else str(Path(p.get('cwd') or os.getcwd())/token)
                for expanded in glob.glob(pattern):
                    candidate=Path(expanded)
                    if candidate.name == 'monitor_queue_ack.py': return True
                    if candidate.exists() and canonical.exists() and os.path.samefile(candidate,canonical): return True
            except (OSError,RuntimeError):
                pass
        if token != command and any(char.isspace() for char in token) and _monitor_ack_script_mentioned(token,depth+1): return True
        if token != command and ('monitor_queue_' in token or 'ack.py' in token) and _monitor_ack_script_mentioned(token,depth+1): return True
    return False
def _monitor_ack_options(parts,start):
    values={key:[] for key in ('--session-id','--queue','--record-id','--note','--status')}
    all_count=0; idx=start
    while idx < len(parts):
        token=parts[idx]
        if token == '--all':
            all_count+=1; idx+=1; continue
        if not token.startswith('--'): return None
        if '=' in token:
            key,value=token.split('=',1)
        else:
            key=token
            if key not in values or idx+1 >= len(parts): return None
            idx+=1; value=parts[idx]
            if value.startswith('--'): return None
        if key not in values or not value: return None
        values[key].append(value); idx+=1
    if len(values['--session-id']) != 1 or len(values['--queue']) != 1 or len(values['--note']) != 1: return None
    if len(values['--status']) > 1 or (values['--status'] and values['--status'][0] not in {'acknowledged','resolved'}): return None
    if all_count > 1 or bool(all_count) == bool(values['--record-id']): return None
    return values, bool(all_count)
def _resolve_monitor_ack_path(value):
    path=Path(value).expanduser()
    base=Path(p.get('cwd') or os.getcwd())
    return (base/path).resolve() if not path.is_absolute() else path.resolve()
def _trusted_monitor_ack_python(value):
    path=Path(value)
    if not path.is_absolute() or path.parent not in {Path('/bin'),Path('/usr/bin'),Path('/usr/local/bin')}: return False
    if not re.fullmatch(r'python(?:3(?:\.\d+)?)?',path.name): return False
    try:
        link_info=path.lstat()
        target_info=path.resolve(strict=True).stat()
    except (OSError,RuntimeError):
        return False
    if link_info.st_uid != 0 or target_info.st_uid != 0: return False
    if stat.S_IMODE(target_info.st_mode) & 0o022: return False
    return stat.S_ISREG(target_info.st_mode)
def _is_monitor_ack_command(command,blocking_records):
    if not command: return False
    if not _monitor_ack_shell_safe(command): return False
    try: parts=shlex.split(command)
    except ValueError: return False
    if not parts: return False
    exe=parts[0]
    exe_name=Path(exe).name
    if re.fullmatch(r'python(?:\d+(?:\.\d+)?)?',exe_name):
        if not _trusted_monitor_ack_python(exe): return False
        if len(parts) < 2: return False
        script=parts[1]; option_start=2
    else:
        return False
    canonical=codex_script_path('monitor_queue_ack.py').resolve()
    try:
        if _resolve_monitor_ack_path(script) != canonical: return False
    except Exception:
        return False
    parsed=_monitor_ack_options(parts,option_start)
    if parsed is None: return False
    values,ack_all=parsed
    if not blocking_records: return False
    try: queue=_resolve_monitor_ack_path(values['--queue'][0])
    except Exception: return False
    if not Path(values['--queue'][0]).expanduser().is_absolute(): return False
    session=values['--session-id'][0]
    matching=[record for record in blocking_records if record_session_id(record) == session and _resolve_monitor_ack_path(str(record.get('_queue_path') or '')) == queue]
    if not matching: return False
    if ack_all: return True
    pending_ids={str(record.get('_record_id') or '') for record in matching}
    return bool(values['--record-id']) and all(record_id in pending_ids for record_id in values['--record-id'])
def _stage3_reconciliation_gate(record):
    return str(record.get('category') or '').lower() == 'stage3_debate_reconciliation'
def _current_campaign_round():
    ad=active_artifact_dir(repo,p)
    state=load_json(ad/'state.json') if ad else None
    campaign=state.get('campaign') if isinstance(state,dict) else None
    current=campaign.get('current_round') if isinstance(campaign,dict) else None
    return current if isinstance(current,int) and not isinstance(current,bool) and current > 0 else None
def _payload_paths():
    ti=_tool_input()
    paths=[]
    for key in ('file_path','filePath','path','target_file','targetFile'):
        value=ti.get(key)
        if isinstance(value,str) and value:
            resolved=_resolve_payload_path(value)
            if resolved is not None:
                paths.append(resolved)
    for text in _payload_texts():
        for match in re.findall(r'^\*\*\* (?:Add|Update|Delete) File:\s+(.+)$',text,flags=re.MULTILINE):
            resolved=_resolve_payload_path(match)
            if resolved is not None:
                paths.append(resolved)
    seen=[]
    for path in paths:
        if path not in seen:
            seen.append(path)
    return seen
def _current_round_proposals_dir():
    ad=active_artifact_dir(repo,p)
    current_round=_current_campaign_round()
    if ad is None or current_round is None:
        return None
    return (ad/'rounds'/str(current_round)/'debate'/'proposals').resolve()
def _path_under(path,base):
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
def _mentions_debate_proposal_artifact():
    base=_current_round_proposals_dir()
    if base is None:
        return False
    return any(_path_under(path,base) for path in _payload_paths())
def _mentions_stage3_transition_or_reconciliation_artifact():
    haystack='\n'.join(_payload_texts())
    ad=active_artifact_dir(repo,p)
    current_round=_current_campaign_round()
    if ad is not None and current_round is not None:
        blocked_paths={
            (ad/'state.json').resolve(),
            (ad/'rounds'/str(current_round)/'debate'/'summary.md').resolve(),
            (ad/'debate'/f'campaign_round_{current_round}'/'summary.md').resolve(),
            (ad/'debate'/f'campaign_round_{current_round}'/'selected_winners.json').resolve(),
        }
        if any(path in blocked_paths for path in _payload_paths()):
            return True
    blocked=[
        r'(?:^|[\\/\s\'"`])state\.json(?:$|[\s\'"`])',
        r'(?:^|[\\/\s\'"`])rounds[\\/]\d+[\\/]debate[\\/]summary\.md(?:$|[\s\'"`])',
        r'(?:^|[\\/\s\'"`])debate[\\/]campaign_round_\d+[\\/]summary\.md(?:$|[\s\'"`])',
        r'(?:^|[\\/\s\'"`])debate[\\/]campaign_round_\d+[\\/]selected_winners\.json(?:$|[\s\'"`])',
        r'current_stage',
        r'selected_winners',
        r'selection_rationale',
        r'rounds_completed',
        r'completed_at',
    ]
    return any(re.search(pattern,haystack) for pattern in blocked)
def _tool_may_continue_under_stage3_reconciliation_gate():
    if is_static_inspection_command(cmd):
        return True
    return _mentions_debate_proposal_artifact() and not _mentions_stage3_transition_or_reconciliation_artifact()
pending=blocking_monitor_records(repo,p)
blocking=[]; monitor_ack_allowed=False
if pending:
    blocking=[record for record in pending if not (_stage3_reconciliation_gate(record) and _tool_may_continue_under_stage3_reconciliation_gate())]
    monitor_ack_allowed=_is_monitor_ack_command(cmd,blocking) if blocking else False
    if blocking and os.environ.get('AMMO_MONITOR_ALLOW_UNACKED')!='1' and not monitor_ack_allowed:
        if cmd and 'monitor_queue_ack.py' not in cmd and is_static_inspection_command(cmd):
            raise SystemExit(0)
        first=str(blocking[0].get('_record_id') or '<record_id>')
        queue=str(blocking[0].get('_queue_path') or '<queue_path>')
        sid=record_session_id(blocking[0]) or session_id_from_payload(p,repo) or '<target_session_id>'
        ack_script=codex_script_path('monitor_queue_ack.py')
        ack_cmd='/usr/bin/python3 '+shlex.quote(str(ack_script))+' --session-id '+shlex.quote(sid)+' --queue '+shlex.quote(queue)+' --record-id '+shlex.quote(first)+' --note "<what changed or why rebutted>"'
        block_pretool('Pending AMMO monitor intervention requires acknowledgement before the next intercepted tool call.\n'+format_monitor_records(blocking,limit=2)+'\nTo acknowledge after reading, run: '+ack_cmd); raise SystemExit(0)
if monitor_ack_allowed: raise SystemExit(0)
if cmd and _monitor_ack_script_mentioned(cmd):
    if not pending and _monitor_ack_file_inspection(cmd): raise SystemExit(0)
    block_pretool('AMMO monitor acknowledgement denied: no matching current blocking record for the exact session, absolute queue, and record binding.'); raise SystemExit(0)
if not cmd: raise SystemExit(0)
if not is_ammo_context(cmd,repo,p): raise SystemExit(0)
if is_static_inspection_command(cmd): raise SystemExit(0)
if '.claude/worktrees' in cmd: block_pretool('Use .codex/worktrees, not .claude/worktrees: those are a Claude Code campaign\'s track worktrees.'); raise SystemExit(0)
warnings=[]
if command_invokes_vllm_bench_latency(cmd) and 'run_vllm_bench_latency_sweep.py' not in cmd: warnings.append('AMMO reminder: raw vllm bench latency detected. Use run_vllm_bench_latency_sweep.py for official AMMO evidence.')
def _is_neuron_runtime():
    return (
        os.environ.get('AMMO_ACCELERATOR_KIND','').lower() == 'neuron'
        or os.environ.get('AMMO_RUNTIME_PROFILE','').lower() in ('pumice-neuron','vllm-neuron')
        or 'NEURON_VISIBLE_DEVICES' in os.environ
        or 'NEURON_RT_VISIBLE_CORES' in os.environ
    )
if not _is_neuron_runtime() and any(re.search(x,cmd) for x in [r'TORCH_COMPILE_DISABLE\s*=\s*1',r'VLLM_TORCH_COMPILE_LEVEL\s*=\s*[01]\b',r'--enforce-eager\b',r'--disable-cuda-graph\b',r'(?:^|\s)--no-compile(?:[\s=]|$)',r'PUMICE_COMPILE_TARGET\s*=\s*["\']?0["\']?(?:[\s;&|]|$)']) and os.environ.get('AMMO_ALLOW_NONPARITY')!='1' and 'AMMO_ALLOW_NONPARITY=1' not in cmd: warnings.append('AMMO reminder: command appears to disable production parity. Official AMMO evidence should use CUDA graphs/torch.compile parity.')
def _profiles_official_sweep(command):
    if 'run_vllm_bench_latency_sweep.py' not in command:
        return False
    if not any(flag in command for flag in ('--nsys-profile', '--torch-profile', '--neuron-profile', '--nki-profile', '--neuron-explorer-profile')):
        return False
    slot_match=re.search(r'--slot(?:=|\s+)(["\']?)([^\s"\']+)\1',command)
    if slot_match:
        slot=slot_match.group(2)
        return (
            slot in {'baseline','integration','golden_capture'}
            or slot.startswith(('opt/','opt_correctness/'))
        )
    # Preserve the pre-v2 spelling for older campaign commands.
    return bool(re.search(r'--mode(?:=|\s+)(opt|optimized|compare|integration)(?:\s|$)',command))
if _profiles_official_sweep(cmd): block_pretool('Profiled sweep output is not admissible official E2E timing or correctness evidence. Use profiling, opt_profiling/{op_id}, or post_ship_profiling slots.'); raise SystemExit(0)
def _unreserved_gpu_sentinel():
    sid=state_session_id(repo,p) or os.environ.get('AMMO_SESSION_ID') or session_id_from_payload(p,repo)
    if not sid: return None
    safe=re.sub(r'[^A-Za-z0-9_.-]+','_',str(sid))[:96] or 'session'
    root=Path(os.environ.get('AMMO_GPU_RES_DIR') or '/tmp/ammo_gpu_res').expanduser()
    return root/f'codex_unreserved_gpu_guard_{safe}.seen'
def _has_explicit_no_accelerator(command):
    # CUDA documents an empty no-device value. Neuron does not; empty or
    # unset Neuron visibility may expose all cores.
    return bool(re.search(r'CUDA_VISIBLE_DEVICES\s*=\s*(?:""|\'\')',command))
if command_invokes_gpu_heavy_tool(cmd) and 'gpu_reservation.py' not in cmd and not _has_explicit_no_accelerator(cmd) and os.environ.get('AMMO_ALLOW_UNRESERVED_GPU')!='1' and 'AMMO_ALLOW_UNRESERVED_GPU=1' not in cmd:
    sentinel=_unreserved_gpu_sentinel()
    if sentinel is None:
        raise SystemExit(0)
    first_notice=not sentinel.exists()
    try:
        sentinel.parent.mkdir(parents=True,exist_ok=True)
        sentinel.touch(exist_ok=True)
    except Exception:
        pass
    if first_notice:
        if _is_neuron_runtime():
            reason='Accelerator-heavy AMMO command detected without reservation. For vLLM or serving-level profiling, use NVD=$(python '+shlex.quote(str(script_path('gpu_reservation.py')))+' reserve --num-resources N) and NEURON_VISIBLE_DEVICES=$NVD. Direct single-process NKI/runtime commands may use the same range as NEURON_RT_VISIBLE_CORES. Empty Neuron visibility is not a no-core sentinel. Use AMMO_ALLOW_UNRESERVED_GPU=1 only with justification. This warning fires only once per session.'
        else:
            reason='GPU-heavy AMMO command detected without reservation. Use '+str(script_path('gpu_reservation.py'))+' reserve, CUDA_VISIBLE_DEVICES="" for explicit no-GPU commands, or AMMO_ALLOW_UNRESERVED_GPU=1 with justification. This warning fires only once per session.'
        if warnings:
            reason += '\n\n' + '\n'.join(warnings)
        block_pretool(reason)
        raise SystemExit(0)
if warnings:
    additional_context('PreToolUse','\n'.join(warnings))
