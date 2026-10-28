"""CPU-only P/D contract tests. Synthetic timing is never performance evidence."""
from __future__ import annotations

import contextlib
import copy
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from serving_contract import (Arm, Command, ContractError, ServingSpec, contained,
                              digest, file_digest, read_json, write_json)
from serving_metrics import compare_windows, percentile, sign_pvalue, summarize_window
from serving_evidence import (incumbent_path, opportunity_decision, promote, seal,
                             validate_activation, validate_component, validate_mechanism,
                             verify_result, verify_seal)
from deployment_ops import Deployment, format_command, stop_group, spawn_owned
from run_serving_sweep import execute


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'model.py').write_text('# source identity fixture\n')
    driver = root / 'driver.py'
    driver.write_text('''import json,os,sys,pathlib
args=sys.argv[1:]
if args[0]=='bench':
    target=pathlib.Path(args[1])/args[2]
    n=int(args[3]); value=0.1
    # Test-only: choose synthetic arm timing from the path, not a model run.
    if target.parent.parent.name=='candidate': value=0.08
    raw={'duration':40.0, 'completed':n, 'failed':0,
         'input_lens':[int(args[8]) if len(args)>8 else 10]*n, 'output_lens':[3]*n, 'ttfts':[value]*n,
         'latencies':[value+0.02]*n, 'itls':[[0.01,0.01] for _ in range(n)],
         'queue_times':[0.0]*n, 'errors':['']*n,'request_ids':[args[7]+str(i) for i in range(n)]}
    target.write_text(json.dumps(raw))
else:
    context=json.loads(pathlib.Path(os.environ['AMMO_SERVING_CONTEXT']).read_text())
    output=pathlib.Path(os.environ['AMMO_SERVING_OUTPUT'])
    (output.parent/'primary.txt').write_text('SYNTHETIC CPU CONTRACT FIXTURE; NOT PERFORMANCE EVIDENCE')
    def digest(x):
        import hashlib
        return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    result={'run_id':context['run_id'],'artifacts':['primary.txt']}
    if args[0]=='component': result.update(tests_run=4,failures=0)
    elif args[0]=='mechanism':
        result.update(baseline_arm_sha256=digest(context['baseline_arm']),candidate_arm_sha256=digest(context['candidate_arm']),
          buckets=[{'id':'b1','scope':'production_boundary','boundary':'synthetic test boundary',
                    'baseline_s':[1,1,1], 'candidate_s':[0.8,0.8,0.8]}])
    elif args[0]=='timing_proof':
        import hashlib, shutil
        sp=context['serving_spec']
        result.update(schema_version='ammo_timing_proof/1',workload_id=context['workload_id'],phase=context['phase'],
          profiled=False,contract_sha256=context['contract_sha256'],arm_sha256=context['arm_sha256'],workers={})
        for w in sp['workers']:
            name=w['name']; src=context['workers'][name]['source']
            module=pathlib.Path(src['path'])/'model.py'; artifact=name+'-loaded.py'
            shutil.copyfile(module,output.parent/artifact);result['artifacts'].append(artifact)
            pr=list(context['processes'][name].values())[0]
            rank=dict(pr,rank=0,imported_source=str(module),source_commit=src['commit'],
              imported_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),imported_artifact=artifact,
              runtime_profile=sp['runtime_profile'],model_revision=sp['model_revision'],dtype=sp['dtype'],
              execution_identity=w['execution_identity'],mechanism_active=True)
            env=context['candidate_arm'].get('envelope')
            if env and w['role'] in env['roles']:
                rank['mechanism_active']=env['min_input_tokens']<=context['workload']['input_tokens']<=env['max_input_tokens']
                rank['fallback_identity_sha256']=env['fallback_identity_sha256']
            result['workers'][name]={'ranks':[rank]}
        if context['phase']=='after':
            raw=json.loads(pathlib.Path(context['raw_result']).read_text())
            ids=[i for i,e in zip(raw['request_ids'],raw['errors']) if not e]
            result['handoff']=dict(request_ids=ids,sent_bytes=100,received_bytes=100,fallback_requests=0,completed_requests=len(ids))
    else:
        result.update(model_revision=context['model_revision'],workers={
          k:dict(pid=v['pid'],source_commit=v['source']['commit'],resource_ids=v['resource_ids'],
             imported_source=str(pathlib.Path(v['source']['path'])/'model.py'),activation_observed=True)
             for k,v in context['workers'].items()},
          handoff=dict(request_ids=['r1'],sent_bytes=100,received_bytes=100,fallback_requests=0,completed_requests=1))
    output.write_text(json.dumps(result))
''')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=CPU fixture', '-c', 'user.email=fixture@localhost',
                    'commit', '-qm', 'synthetic fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    return root, commit


def spec_dict(source):
    root, commit = source
    # Keep all sockets bound until all three ports are chosen. Closing each
    # immediately can let the OS return the same ephemeral port twice.
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(('127.0.0.1', 0))
        ports = [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    return {
        'runtime_profile': 'vllm-cuda', 'model_id': 'test/model', 'model_revision': 'a'*40, 'dtype': 'bf16',
        'endpoint': f'http://127.0.0.1:{ports[2]}',
        'workers': [{'name': name, 'role': role, 'resource_ids': resources,
            'command': {'argv': [sys.executable, '-c', 'import time;time.sleep(60)']},
            'ready_url': f'http://127.0.0.1:{port}/health',
            'execution_identity':{k:'synthetic CPU fixture' for k in ('engine','runtime','compiler','compile_config','cache_policy','graph_mode','transport')}}
            for name, role, resources, port in zip(['p','d','r'], ['prefill','decode','router'], [[0],[1],[]], ports)],
        'workloads': [{'id':'w1','request_rate':1.0,'num_prompts':8,'output_tokens':3,
            'files': {str(root/'driver.py'):file_digest(root/'driver.py')},
            'command': {'argv': [sys.executable, '-S', str(root/'driver.py'), 'bench', '{result_dir}',
                '{result_filename}', '{num_prompts}', '{seed}', '{endpoint}', '{request_rate}', '{request_id_prefix}']}}],
        'adapter_files': {str(root/'driver.py'):file_digest(root/'driver.py')},
        'adapters': {key:{'argv':[sys.executable,'-S',str(root/'driver.py'),key]} for key in ['component','mechanism','activation','timing_proof']},
        'policy': {'objective':'ttft_ms','repetitions':6,'min_duration_s':1,
                   'slo_ms':{'ttft_ms':500,'tpot_ms':100,'itl_ms':100},'correctness_questions':4},
        'startup_timeout_s':2,'max_run_s':120,
    }


@pytest.fixture
def spec(source):
    return ServingSpec.model_validate(spec_dict(source))


def arm_for(spec, source, name='baseline'):
    root, commit = source
    return Arm.model_validate({'id':name,'sources':{w.name:{'path':str(root),'commit':commit} for w in spec.workers}})


def raw_window(n=8, ttft=0.1):
    return {'duration':40.0,'completed':n,'failed':0,'input_lens':[10]*n,'output_lens':[3]*n,
            'ttfts':[ttft]*n,'latencies':[ttft+0.02]*n,'itls':[[0.01,0.01] for _ in range(n)],
            'queue_times':[0.0]*n,'errors':['']*n}


def pairs(spec, baseline=0.1, candidate=0.08):
    return [{'workload_id':w.id,'repetition':i,
             'baseline':raw_window(w.num_prompts,baseline), 'candidate':raw_window(w.num_prompts,candidate)}
            for w in spec.workloads for i in range(spec.policy.repetitions)]


@pytest.mark.parametrize('mutation', [
    lambda d:d.update(unknown=True),
    lambda d:d.update(manager='remote'),
    lambda d:d.update(runtime_profile='vllm-pd'),
    lambda d:d.update(model_revision='main'),
    lambda d:d['workers'][1].update(resource_ids=[0]),
    lambda d:d['workers'][0].update(resource_ids=[0,0]),
    lambda d:d['workers'][0].update(resource_ids=[]),
    lambda d:d['workers'][2].update(resource_ids=[2]),
    lambda d:d['workers'][0].update(name='../escape'),
    lambda d:d['workers'][0].update(ready_url='http://example.com:80/health'),
    lambda d:d['workers'][0].update(env={'CUDA_VISIBLE_DEVICES':'9'}),
    lambda d:d['workers'][0].update(env={'AMMO_SESSION_ID':'other'}),
    lambda d:d['policy'].update(repetitions=2),
    lambda d:d['policy'].update(min_improvement_pct=True),
    lambda d:d['policy'].update(slo_ms={'ttft_ms':True}),
    lambda d:d['workloads'][0]['command']['argv'].append('--profile=true'),
    lambda d:d['policy'].update(slo_ms={}),
    lambda d:d['policy'].update(slo_ms={'ttft_ms':float('nan')}),
    lambda d:d['workloads'][0].update(request_rate=float('inf')),
    lambda d:d['workloads'][0]['command']['argv'].append('--profile'),
    lambda d:d['workloads'][0]['command']['argv'].append('--max-concurrency=8'),
    lambda d:d['adapters'].pop('activation'),
])
def test_target_rejects_unsafe_contract(source, mutation):
    data = spec_dict(source)
    mutation(data)
    with pytest.raises((ValidationError, ValueError)):
        ServingSpec.model_validate(data)


def test_source_must_be_clean(spec, source):
    arm = arm_for(spec, source)
    arm.verify(spec)
    (source[0]/'model.py').write_text('changed\n')
    with pytest.raises(ContractError, match='dirty'):
        arm.verify(spec)


def test_inputs_are_bound_by_content(spec, source):
    spec.verify_files()
    (source[0]/'driver.py').write_text('changed\n')
    with pytest.raises(ContractError, match='frozen input'):
        spec.verify_files()


def test_legacy_neuron_range_rules(source):
    data=spec_dict(source)
    data['runtime_profile']='vllm-neuron';data['lnc']=2
    data['workers'][0]['resource_ids']=[0,2]
    data['workers'][1]['resource_ids']=[3]
    with pytest.raises(ValidationError, match='contiguous'):
        ServingSpec.model_validate(data)


def test_metrics_recompute_instead_of_trusting_summary(spec):
    raw=raw_window()
    raw.update(request_goodput=9999, p99_ttft_ms=0)
    result=summarize_window(raw,spec.workloads[0],spec.policy)
    assert result['ttft_ms']==100
    assert result['goodput_rps']==8/40
    assert result['tpot_ms']==pytest.approx(10)


@pytest.mark.parametrize('mutation',[
    lambda d:d['ttfts'].pop(), lambda d:d.update(completed=7),
    lambda d:d['latencies'].__setitem__(0,0.01),
    lambda d:d['ttfts'].__setitem__(0,float('nan')),
    lambda d:d['ttfts'].__setitem__(0,True),
    lambda d:d['queue_times'].__setitem__(0,0.1),
    lambda d:d['output_lens'].__setitem__(0,1),
    lambda d:d['itls'].__setitem__(0,[]),
    lambda d:d.update(duration=0), lambda d:d.pop('queue_times'),
    lambda d:d.update(failed=1), lambda d:d['errors'].__setitem__(0,None),
])
def test_bad_raw_evidence_is_not_noise(spec,mutation):
    raw=raw_window();mutation(raw)
    with pytest.raises(ContractError): summarize_window(raw,spec.workloads[0],spec.policy)


def test_failed_requests_are_kept_in_denominator(spec):
    raw=raw_window();raw.update(completed=7,failed=1);raw['errors'][0]='timeout';raw['output_lens'][0]=0
    result=summarize_window(raw,spec.workloads[0],spec.policy)
    assert result['error_rate']==pytest.approx(1/8)
    assert not result['constraints_pass']
    assert result['goodput_rps']==7/40


def test_no_missing_or_duplicate_cells(spec):
    windows=pairs(spec)
    for changed in [windows[:-1], windows+[windows[0]]]:
        with pytest.raises(ContractError,match='missing, extra, or duplicate'):
            compare_windows(spec,changed)


def test_positive_and_negative_verdicts(spec):
    assert compare_windows(spec,pairs(spec))['status']=='PASS'
    assert compare_windows(spec,pairs(spec,candidate=.1))['status']=='FAIL'
    assert compare_windows(spec,pairs(spec,candidate=.2))['status']=='FAIL'


def test_one_bad_window_cannot_hide_in_average(spec):
    windows=pairs(spec);windows[-1]['candidate']=raw_window(ttft=.11)
    assert compare_windows(spec,windows)['rows'][0]['status']=='REGRESSED'


def test_other_workloads_are_guardrails(spec):
    data=spec.model_dump();other=copy.deepcopy(data['workloads'][0]);other['id']='w2';data['workloads'].append(other)
    spec=ServingSpec.model_validate(data);windows=pairs(spec)
    for w in windows:
        if w['workload_id']=='w2':w['candidate']=raw_window(ttft=.2)
    assert compare_windows(spec,windows)['status']=='FAIL'


def test_multiple_testing_correction(spec):
    data=spec.model_dump()
    data['workloads']=[{**data['workloads'][0], 'id':f'w{i}'} for i in range(4)]
    spec=ServingSpec.model_validate(data)
    # Six uniformly positive paired windows give p=1/64. Four tests require
    # p<=.0125, so this is correctly unresolved instead of a claimed win.
    result=compare_windows(spec,pairs(spec))
    assert result['status']=='FAIL'
    assert result['rows'][0]['p_value']==1/64


def test_sign_test_and_percentile():
    assert sign_pvalue([1]*6,.5)==1/64
    assert sign_pvalue([0]*6,.5)==1
    assert percentile([1,2,3,4],50)==2.5


@pytest.mark.parametrize('name',['../escape','/absolute','x/../../escape'])
def test_artifact_reference_cannot_escape(tmp_path,name):
    with pytest.raises(ContractError):contained(tmp_path,name)


def test_unknown_opportunity_never_stops(spec):
    mining={'contract_sha256':spec.checksum,'incumbent_sha256':'x','objective':'ttft_ms',
            'coverage_complete':True,'opportunities':[{'upper_bound_improvement_pct':None,'evidence':['trace']} ]}
    assert opportunity_decision(mining,spec,'x')=='UNRESOLVED'
    mining['opportunities'][0]['upper_bound_improvement_pct']=.1
    assert opportunity_decision(mining,spec,'x')=='STOP'
    mining['opportunities'][0]['upper_bound_improvement_pct']=2
    assert opportunity_decision(mining,spec,'x')=='CONTINUE'
    with pytest.raises(ContractError,match='stale'):opportunity_decision(mining,spec,'different')


class FakeDeployment(Deployment):
    """No accelerator work. Exercises the real driver and artifact pipeline."""
    def __enter__(self):
        self.output.mkdir(parents=True,exist_ok=False)
        write_json(self.context_path,{'run_id':self.run_id,'model_revision':self.spec.model_revision,
            'contract_sha256':self.spec.checksum,'arm_sha256':self.arm.checksum,
            'workers':{w.name:{'role':w.role,'pid':100+i,'resource_ids':w.resource_ids,
                 'source':self.arm.sources[w.name].model_dump()} for i,w in enumerate(self.spec.workers)}})
        return self
    def snapshot_processes(self):
        return {w.name: {str(100+i): {'pid':100+i,'start_time':1.0,
            'environment_sha256':digest(self.environment(w)), 'executable_sha256':'e'*64}}
            for i,w in enumerate(self.spec.workers)}
    def check_alive(self):
        if time.monotonic()>=self.deadline:raise ContractError('fake deadline')
    def __exit__(self,*args):pass


@contextlib.contextmanager
def fake_reserve(spec,owner):
    yield


def fake_correctness(deployment,spec):
    n=spec.policy.correctness_questions
    return {'answers':['The answer is 3.']*n,'labels':[3]*n,'num_questions':n,
            'max_tokens':spec.policy.correctness_max_tokens,'suite_sha256':'d'*64,
            'run_id':deployment.run_id}


def run_fake(spec,source,directory,kind,incumbent=None,correctness=fake_correctness):
    return execute(spec,arm_for(spec,source,kind),directory,kind,incumbent=incumbent,
        reserve=fake_reserve,deployment_class=FakeDeployment,correctness=correctness)


@pytest.fixture
def captured(spec,source,tmp_path):
    root=tmp_path/'serving';baseline=root/'baseline'
    run_fake(spec,source,baseline,'baseline')
    return root,baseline


def test_full_five_gate_pipeline_is_cpu_only_fixture(spec,source,captured):
    root,baseline=captured
    candidate=root/'candidate'
    result=run_fake(spec,source,candidate,'candidate',baseline)
    assert result['status']=='PASS'
    assert read_json(candidate/'gate_events.json')['events']==['5.1a','5.1b','5.2','5.3a','5.3b']
    assert verify_result(candidate,spec,baseline)['status']=='PASS'
    # The cached summary is never authoritative.
    write_json(candidate/'summary.json',{'status':'FAIL'})
    assert verify_result(candidate,spec,baseline)['status']=='PASS'


def test_mechanism_context_names_the_sealed_incumbent(spec,source,captured):
    root,baseline=captured
    candidate=root/'candidate';run_fake(spec,source,candidate,'candidate',baseline)
    context=read_json(candidate/'mechanism/context.json')
    incumbent=Path(context['incumbent'])
    # The collector runs in the arm's source worktree, so the path must not be cwd-relative.
    assert incumbent.is_absolute() and incumbent.is_dir() and incumbent.resolve()==baseline.resolve()
    assert context['incumbent_seal_sha256']==digest(read_json(incumbent/'seal.json'))


@pytest.mark.parametrize('mutate',[lambda c:c.update(incumbent_seal_sha256='f'*64),
                                   lambda c:c.pop('incumbent_seal_sha256')])
def test_mechanism_bound_to_another_incumbent_never_verifies(spec,source,captured,mutate):
    root,baseline=captured
    candidate=root/'candidate';run_fake(spec,source,candidate,'candidate',baseline)
    path=candidate/'mechanism/context.json';context=read_json(path);mutate(context);write_json(path,context)
    seal(candidate,spec,arm_for(spec,source,'candidate'),kind='candidate',parent=digest(read_json(baseline/'seal.json')))
    with pytest.raises(ContractError,match='mechanism context'):verify_result(candidate,spec,baseline)


def test_mechanism_failure_bound_to_another_incumbent_never_verifies(spec,source,captured,monkeypatch):
    import run_serving_sweep as runner
    real=runner.run_adapter
    def slower(name,deployment,directory,*args,**kwargs):
        result=real(name,deployment,directory,*args,**kwargs)
        if name=='mechanism':
            result['buckets'][0].update(candidate_s=[1.2]*3,regression_explanation='synthetic slower fixture')
            write_json(directory/'result.json',result)
        return result
    monkeypatch.setattr(runner,'run_adapter',slower)
    root,baseline=captured;output=root/'slower'
    result=run_fake(spec,source,output,'candidate',baseline)
    assert result['status']=='FAIL' and result['failed_gate']=='5.2'
    assert verify_result(output,spec,baseline)==result
    path=output/'mechanism/context.json';context=read_json(path)
    context['incumbent_seal_sha256']='f'*64;write_json(path,context)
    seal(output,spec,arm_for(spec,source,'candidate'),kind='candidate_failure',parent=digest(read_json(baseline/'seal.json')))
    with pytest.raises(ContractError,match='mechanism context'):verify_result(output,spec,baseline)


def test_correctness_failure_stops_before_mechanism_or_timing(spec,source,captured):
    root,baseline=captured
    def wrong(deployment,spec):
        value=fake_correctness(deployment,spec);value['answers']=['0']*spec.policy.correctness_questions;return value
    candidate=root/'bad'
    result = run_fake(spec,source,candidate,'candidate',baseline,wrong)
    assert result['status'] == 'FAIL' and result['failed_gate'] == '5.1b'
    assert verify_result(candidate, spec, baseline) == result
    assert not (candidate/'mechanism').exists()
    assert not (candidate/'timing').exists()
    assert (candidate/'seal.json').exists()
    assert (candidate/'failure.json').exists()


def test_tampering_with_raw_results_breaks_the_seal(spec,captured):
    root,baseline=captured
    record=read_json(baseline/'baseline_windows.json')['windows'][0]
    raw=baseline/record['raw'];data=read_json(raw);data['ttfts']=[.001]*8;write_json(raw,data)
    with pytest.raises(ContractError,match='changed or missing'):
        verify_result(baseline,spec)


def test_incumbent_promotion_requires_integration_and_cas(spec,source,captured):
    root,baseline=captured;pointer=root/'incumbent.json'
    first=promote(pointer,baseline,spec,expected=None)
    assert incumbent_path(pointer,spec)==baseline
    candidate=root/'candidate';run_fake(spec,source,candidate,'candidate',baseline)
    with pytest.raises(ContractError,match='integration evidence'):
        promote(pointer,candidate,spec,expected=first['evidence_sha256'])
    integration=root/'integration';run_fake(spec,source,integration,'integration',baseline)
    with pytest.raises(ContractError,match='incumbent changed'):
        promote(pointer,integration,spec,expected='stale')
    second=promote(pointer,integration,spec,expected=first['evidence_sha256'])
    assert second['previous_evidence_sha256']==first['evidence_sha256']
    assert read_json(root/'original.json')==first


def test_incomplete_baseline_never_verifies(spec,source,tmp_path):
    arm=arm_for(spec,source)
    root=tmp_path/'incomplete';root.mkdir()
    seal(root,spec,arm,kind='baseline',parent=None)
    with pytest.raises(ContractError,match='empty evidence manifest'):
        verify_result(root,spec)


def test_no_duplicate_output_slots(spec,source,captured):
    root,baseline=captured
    with pytest.raises(FileExistsError):run_fake(spec,source,baseline,'baseline')


def test_activation_requires_real_handoff(spec,source,captured):
    root,baseline=captured
    raw=read_json(baseline/'activation/result.json');ctx=read_json(baseline/'activation/context.json')
    arm=arm_for(spec,source)
    raw['handoff']['fallback_requests']=1
    with pytest.raises(ContractError,match='fallback'):validate_activation(raw,ctx,spec,arm)
    raw['handoff']['fallback_requests']=0;raw['workers']['p']['source_commit']='b'*40
    with pytest.raises(ContractError,match='worker identity'):validate_activation(raw,ctx,spec,arm)


def test_shell_metacharacters_remain_plain_argv():
    command=Command(argv=['echo','$(touch /tmp/not-executed)','{endpoint}'])
    assert format_command(command,{'endpoint':'a; b'})==['echo','$(touch /tmp/not-executed)','a; b']


def test_stop_group_cleans_owned_process():
    proc=spawn_owned([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)
    stop_group(proc,grace_s=.05)
    assert proc.poll() is not None


def test_start_failure_rolls_back_started_workers(spec,source,tmp_path):
    # Start real CPU HTTP processes. This tests lifecycle only, not inference.
    data=spec.model_dump()
    for worker in data['workers']:
        port=worker['ready_url'].split(':')[2].split('/')[0]
        worker['ready_url']=worker['ready_url'].replace('/health','/')
        worker['command']['argv']=[sys.executable,'-m','http.server',port,'--bind','127.0.0.1']
    data['workers'][1]['command']['argv']=[sys.executable,'-c','raise SystemExit(9)']
    spec=ServingSpec.model_validate(data)
    d=Deployment(spec,arm_for(spec,source),tmp_path/'real-processes',deadline=time.monotonic()+10)
    with pytest.raises(ContractError,match='exited'):
        with d:pass
    assert not d.processes
    # The first listener must be gone after failure of the second worker.
    with socket.socket() as sock:
        sock.settimeout(.2)
        port=int(data['workers'][0]['ready_url'].split(':')[2].split('/')[0])
        assert sock.connect_ex(('127.0.0.1',port)) != 0


def test_reservation_survives_uncertain_cleanup(spec, monkeypatch):
    import types
    from deployment_ops import CleanupError, reserve_deployment
    releases, writes = [], []
    fake = types.SimpleNamespace(
        _discover_session_resources=lambda: [0, 1],
        write_reservation=lambda *a, **k: writes.append((a, k)),
        release_by_session=lambda *a, **k: releases.append((a, k)))
    monkeypatch.setitem(sys.modules, 'gpu_reservation', fake)
    monkeypatch.setenv('AMMO_SESSION_ID', 'test-owner')
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE', 'vllm-cuda')
    with pytest.raises(CleanupError):
        with reserve_deployment(spec, 'test-owner:pd:unique'):
            raise CleanupError('owned worker could not be stopped')
    assert len(writes) == 1
    assert not releases
    # Ordinary failures still release after successful cleanup.
    with pytest.raises(ContractError):
        with reserve_deployment(spec, 'test-owner:pd:other'):
            raise ContractError('benchmark failure after confirmed teardown')
    assert len(releases) == 1


def test_out_of_pool_resources_never_reserve(spec, monkeypatch):
    import types
    from deployment_ops import reserve_deployment
    writes = []
    fake = types.SimpleNamespace(_discover_session_resources=lambda: [0],
        write_reservation=lambda *a, **k: writes.append(a))
    monkeypatch.setitem(sys.modules, 'gpu_reservation', fake)
    monkeypatch.setenv('AMMO_SESSION_ID', 'test-owner')
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE', 'vllm-cuda')
    with pytest.raises(ContractError, match='outside its session'):
        with reserve_deployment(spec, 'test-owner:pd:unique'):
            pytest.fail('out-of-pool allocation must not yield')
    assert not writes


@pytest.mark.parametrize('field,value', [('profiled', True), ('seed', 987), ('arm_sha256', 'f'*64)])
def test_changed_receipt_rejected_even_after_reseal(spec, source, captured, field, value):
    root, baseline = captured
    window = read_json(baseline/'baseline_windows.json')['windows'][0]
    receipt = (baseline / window['raw']).with_name('invocation.json')
    raw = read_json(receipt); raw[field] = value; write_json(receipt, raw)
    seal(baseline, spec, arm_for(spec, source), kind='baseline', parent=None)
    with pytest.raises(ContractError, match='timing receipt'):
        verify_result(baseline, spec)


def test_unsealed_adapter_artifact_cannot_be_added_later(spec, captured):
    root, baseline = captured
    path = baseline/'seal.json'
    data = read_json(path); data['files'].pop('activation/primary.txt'); write_json(path, data)
    with pytest.raises(ContractError, match='primary artifact is not sealed'):
        verify_result(baseline, spec)


def test_stale_activation_run_rejected_after_reseal(spec, source, captured):
    root, baseline = captured
    path = baseline/'activation/result.json'
    raw = read_json(path); raw['run_id'] = 'stale'; write_json(path, raw)
    seal(baseline, spec, arm_for(spec, source), kind='baseline', parent=None)
    with pytest.raises(ContractError, match='stale run identity'):
        verify_result(baseline, spec)


def test_duplicate_json_keys_are_not_silently_accepted(tmp_path):
    path = tmp_path/'duplicate.json'; path.write_text('{"status":"FAIL","status":"PASS"}')
    with pytest.raises(ContractError, match='duplicate JSON key'):
        read_json(path)


def test_post_first_token_intervals_cannot_exceed_elapsed_time(spec):
    raw = raw_window(); raw['itls'][0] = [1.0]
    with pytest.raises(ContractError, match='intervals exceed'):
        summarize_window(raw, spec.workloads[0], spec.policy)


def test_cleanup_kills_child_after_foreground_leader_exits(tmp_path):
    from deployment_ops import _group_has_live_members
    marker = tmp_path/'child.json'
    code = ('import subprocess,sys,json,pathlib; '
            'p=subprocess.Popen([sys.executable,"-S","-c","import time;time.sleep(60)"]); '
            f'pathlib.Path({str(marker)!r}).write_text(json.dumps(p.pid))')
    proc = spawn_owned([sys.executable, '-S', '-c', code], start_new_session=True)
    try:
        proc.wait(timeout=10)
        assert marker.exists()
        assert _group_has_live_members(proc.pid)
        stop_group(proc, grace_s=.05)
        assert not _group_has_live_members(proc.pid)
    finally:
        stop_group(proc, grace_s=.05)


@pytest.mark.parametrize('wrapper', [False, True])
def test_serving_verifier_requires_campaign_state_not_batch_fallback(spec, tmp_path, wrapper):
    import verify_validation_gates
    data = spec.model_dump()
    write_json(tmp_path/'target.json', {'serving': data} if wrapper else data)
    with pytest.raises(FileNotFoundError, match='state.json'):
        verify_validation_gates.verify_validation(tmp_path, None)


def test_batch_target_guard_is_backward_compatible():
    from serving_contract import reject_legacy_target
    reject_legacy_target({'target': {'model_id':'test/model'}, 'workload': {}}, 'unit test')


def test_historical_verification_uses_explicit_comparator(spec, source, captured, monkeypatch, capsys):
    import run_serving_sweep
    root, baseline = captured
    candidate = root/'candidate'; run_fake(spec, source, candidate, 'candidate', baseline)
    # This isolated CLI verification does not require any current pointer.
    write_json(root.parent/'target.json', spec.model_dump())
    rc = run_serving_sweep.main(['--artifact-dir',str(root.parent),'--kind','verify',
                                '--result',str(candidate),'--against',str(baseline)])
    assert rc == 0
    assert '"status": "PASS"' in capsys.readouterr().out


@pytest.mark.parametrize('name,model', [('serving.schema.json',ServingSpec), ('serving-arm.schema.json',Arm)])
def test_serving_schema_snapshots_match_models(name, model):
    schema = model.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    stored = read_json(ROOT/'schemas'/name)
    assert stored == schema


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(profiled=True),
    lambda p: p.update(run_id='stale'),
    lambda p: p.update(workload_id='not-the-measured-workload'),
    lambda p: p['workers'].pop('d'),
    lambda p: p['workers']['d']['ranks'][0].update(pid=9999999),
    lambda p: p['workers']['d']['ranks'][0].update(start_time=0),
    lambda p: p['workers']['d']['ranks'][0].update(environment_sha256='f'*64),
    lambda p: p['workers']['d']['ranks'][0].update(executable_sha256='f'*64),
    lambda p: p['workers']['d']['ranks'][0].update(source_commit='f'*40),
    lambda p: p['workers']['d']['ranks'][0].update(imported_source='/wrong/module.py'),
    lambda p: p['workers']['d']['ranks'][0].update(imported_sha256='f'*64),
    lambda p: p['workers']['d']['ranks'][0].update(runtime_profile='vllm-neuron'),
    lambda p: p['workers']['d']['ranks'][0].update(dtype='fp8'),
    lambda p: p['workers']['d']['ranks'][0].update(model_revision='f'*40),
    lambda p: p['workers']['d']['ranks'][0].update(execution_identity={}),
    lambda p: p['workers']['d']['ranks'][0].update(mechanism_active=False),
    lambda p: p['handoff'].update(fallback_requests=1),
    lambda p: p['handoff']['request_ids'].pop(),
    lambda p: p['handoff']['request_ids'].append(p['handoff']['request_ids'][0]),
])
def test_passive_timing_proof_cannot_be_rebound_or_waived(spec, source, captured, mutate):
    root, baseline = captured
    window = read_json(baseline/'baseline_windows.json')['windows'][0]
    path = (baseline/window['raw']).parent/'proof_after'/'result.json'
    value = read_json(path); mutate(value); write_json(path,value)
    seal(baseline,spec,arm_for(spec,source),kind='baseline',parent=None)
    with pytest.raises(ContractError): verify_result(baseline,spec)


def test_missing_passive_proof_stays_blocked(spec, source, captured):
    root, baseline = captured
    window = read_json(baseline/'baseline_windows.json')['windows'][0]
    (baseline/window['raw']).parent.joinpath('proof_before/result.json').unlink()
    seal(baseline,spec,arm_for(spec,source),kind='baseline',parent=None)
    with pytest.raises(ContractError,match='not bound'): verify_result(baseline,spec)


def test_runtime_restarted_mid_window_is_not_a_valid_pair(spec, source, captured):
    root, baseline = captured
    window=read_json(baseline/'baseline_windows.json')['windows'][0]
    folder=(baseline/window['raw']).parent/'proof_after'
    proof=read_json(folder/'result.json');context=read_json(folder/'context.json')
    rank=proof['workers']['d']['ranks'][0]
    rank['start_time']+=1
    context['processes']['d'][str(rank['pid'])]['start_time']+=1
    write_json(folder/'result.json',proof);write_json(folder/'context.json',context)
    seal(baseline,spec,arm_for(spec,source),kind='baseline',parent=None)
    with pytest.raises(ContractError,match='changed during'): verify_result(baseline,spec)


def test_broken_collector_is_not_a_candidate_failure(spec,source,captured,monkeypatch):
    import run_serving_sweep as runner
    root,baseline=captured
    real=runner.run_adapter
    def broken(name,*args,**kwargs):
        if name=='component': raise ContractError('collector did not run')
        return real(name,*args,**kwargs)
    monkeypatch.setattr(runner,'run_adapter',broken)
    output=root/'broken'
    with pytest.raises(ContractError,match='collector did not run'):
        run_fake(spec,source,output,'candidate',baseline)
    assert read_json(output/'failure.json')['status']=='BLOCKED'
    assert not (output/'seal.json').exists()


def test_false_early_failure_cannot_be_resealed_as_fail(spec,source,captured):
    root,baseline=captured
    def wrong(deployment,spec):
        value=fake_correctness(deployment,spec);value['answers']=['0']*spec.policy.correctness_questions;return value
    output=root/'bad';run_fake(spec,source,output,'candidate',baseline,wrong)
    correct=read_json(output/'correctness.json')
    correct['answers']=read_json(baseline/'correctness.json')['answers']
    write_json(output/'correctness.json',correct)
    seal(output,spec,arm_for(spec,source,'candidate'),kind='candidate_failure',parent=digest(read_json(baseline/'seal.json')))
    with pytest.raises(ContractError,match='actually passes'):verify_result(output,spec,baseline)


def test_deployment_does_not_inherit_ambient_loader_flags(spec,source,tmp_path,monkeypatch):
    monkeypatch.setenv('LD_PRELOAD','malicious.so')
    monkeypatch.setenv('PYTHONPATH','/unapproved')
    monkeypatch.setenv('UNRELATED_SECRET','must-not-leak')
    deployment=Deployment(spec,arm_for(spec,source),tmp_path/'owned',deadline=time.monotonic()+10)
    for worker in [None,*spec.workers]:
        env=deployment.environment(worker)
        assert not {'LD_PRELOAD','PYTHONPATH','UNRELATED_SECRET'} & set(env)


def test_gated_arm_needs_real_in_and_out_of_envelope_workloads(spec,source):
    data=arm_for(spec,source,'candidate').model_dump()
    data['envelope']={'roles':['decode'],'max_input_tokens':1024,'predicate_symbol':'module.actual_dispatch',
                      'fallback_identity_sha256':'a'*64}
    arm=Arm.model_validate(data)
    with pytest.raises(ContractError,match='exact input'):arm.validate_contract(spec)
    single=spec.model_copy(update={'workloads':[spec.workloads[0].model_copy(update={'input_tokens':10})]})
    with pytest.raises(ContractError,match='positive and negative'):arm.validate_contract(single)


def test_gated_pass_requires_positive_and_negative_runtime_proof(spec,source,tmp_path):
    from serving_contract import Envelope
    data=spec.model_dump()
    first=data['workloads'][0]; first['input_tokens']=10
    second=copy.deepcopy(first);second['id']='outside';second['input_tokens']=10
    # Two distinct ranges cannot be fabricated using a workload label. The exact
    # production input length is the predicate. First test that both equal shapes
    # are rejected, even though the agent assigned different labels.
    data['workloads'].append(second)
    modified=ServingSpec.model_validate(data)
    arm=arm_for(modified,source,'candidate').model_copy(update={'envelope':Envelope(
        roles=['decode'],min_input_tokens=1,max_input_tokens=10,predicate_symbol='model.dispatch',
        fallback_identity_sha256='a'*64)})
    with pytest.raises(ContractError,match='positive and negative'):arm.validate_contract(modified)



def test_gated_pass_uses_measured_predicate_and_incumbent_fallback(spec,source,tmp_path):
    from serving_contract import Envelope
    data=spec.model_dump()
    first=data['workloads'][0];first['input_tokens']=10;first['command']['argv'].append('10')
    second=copy.deepcopy(first);second.update(id='outside',input_tokens=20);second['command']['argv'][-1]='20'
    data['workloads'].append(second)
    gated_spec=ServingSpec.model_validate(data)
    baseline=tmp_path/'gated-baseline'
    run_fake(gated_spec,source,baseline,'baseline')
    arm=arm_for(gated_spec,source,'candidate').model_copy(update={'envelope':Envelope(
        roles=['decode'],min_input_tokens=1,max_input_tokens=10,predicate_symbol='model.dispatch',
        fallback_identity_sha256='a'*64)})
    output=tmp_path/'gated-candidate'
    result=execute(gated_spec,arm,output,'candidate',incumbent=baseline,
        reserve=fake_reserve,deployment_class=FakeDeployment,correctness=fake_correctness)
    assert result['status']=='GATED_PASS'
    assert result['envelope']['max_input_tokens']==10
    outside=next(row for row in read_json(output/'paired_windows.json')['windows'] if row['workload_id']=='outside')
    proof=(output/outside['candidate']).parent/'proof_after/result.json'
    raw=read_json(proof);raw['workers']['d']['ranks'][0]['fallback_identity_sha256']='f'*64
    write_json(proof,raw)
    seal(output,gated_spec,arm,kind='candidate',parent=digest(read_json(baseline/'seal.json')))
    with pytest.raises(ContractError,match='pinned incumbent fallback'):verify_result(output,gated_spec,baseline)


def test_old_request_ids_do_not_establish_current_handoff(spec,source,captured):
    _,baseline=captured
    window=read_json(baseline/'baseline_windows.json')['windows'][0]
    path=baseline/window['raw'];raw=read_json(path);raw['request_ids']=[str(i) for i in range(len(raw['errors']))]
    write_json(path,raw)
    proof_path=path.parent/'proof_after/result.json';proof=read_json(proof_path)
    proof['handoff']['request_ids']=raw['request_ids'];write_json(proof_path,proof)
    seal(baseline,spec,arm_for(spec,source),kind='baseline',parent=None)
    with pytest.raises(ContractError,match='actual deployment'):verify_result(baseline,spec)


def test_reused_pid_is_never_signaled(monkeypatch):
    import deployment_ops, psutil
    from types import SimpleNamespace
    proc = SimpleNamespace(pid=12345, ammo_create_time=1.)
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 2.))
    calls=[]
    monkeypatch.setattr(deployment_ops.os,'killpg',lambda *args:calls.append(args))
    with pytest.raises(deployment_ops.CleanupError,match='reused'):
        stop_group(proc)
    assert not calls


def test_neuron_lnc_is_frozen_and_not_a_worker_override(spec,source,tmp_path,monkeypatch):
    data=spec.model_dump();data.update(runtime_profile='vllm-neuron',lnc=2)
    neuron=ServingSpec.model_validate(data)
    d=Deployment(neuron,arm_for(neuron,source),tmp_path/'lnc',deadline=time.monotonic()+10)
    monkeypatch.setenv('NEURON_LOGICAL_NC_CONFIG','1')
    assert d.environment(neuron.workers[0])['NEURON_LOGICAL_NC_CONFIG']=='2'
    from deployment_ops import reserve_deployment
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE','vllm-neuron')
    with pytest.raises(ContractError,match='LNC differs'):
        with reserve_deployment(neuron,'test-owner'):pass
    data['workers'][0]['env']['NEURON_LOGICAL_NC_CONFIG']='1'
    with pytest.raises(ValidationError,match='override'):
        ServingSpec.model_validate(data)


def test_neuron_requires_explicit_lnc_and_cuda_rejects_it(spec):
    data=spec.model_dump();data['runtime_profile']='vllm-neuron'
    with pytest.raises(ValidationError,match='LNC'):
        ServingSpec.model_validate(data)
    data=spec.model_dump();data['lnc']=2
    with pytest.raises(ValidationError,match='LNC'):
        ServingSpec.model_validate(data)


def test_runtime_profile_sets_group_vllm_and_pumice_runners():
    from serving_contract import CUDA_PROFILES, NEURON_PROFILES
    assert CUDA_PROFILES == ('vllm-cuda', 'pumice-cuda')
    assert NEURON_PROFILES == ('vllm-neuron', 'pumice-neuron')


@pytest.mark.parametrize('profile', ['vllm-cuda', 'pumice-cuda'])
def test_cuda_profiles_bind_owned_gpus_and_reject_lnc(spec, source, tmp_path, profile):
    data = spec.model_dump(); data['runtime_profile'] = profile
    cuda = ServingSpec.model_validate(data)
    d = Deployment(cuda, arm_for(cuda, source), tmp_path/'cuda', deadline=time.monotonic()+10)
    env = d.environment(cuda.workers[1])
    # A pumice-cuda engine worker must see its GPU, never the CPU-only ''.
    assert env['CUDA_VISIBLE_DEVICES'] == '1'
    assert not {'NEURON_VISIBLE_DEVICES', 'NEURON_RT_VISIBLE_CORES', 'NEURON_LOGICAL_NC_CONFIG'} & set(env)
    assert env['AMMO_RUNTIME_PROFILE'] == profile
    assert d.environment(cuda.workers[2])['CUDA_VISIBLE_DEVICES'] == ''
    data['lnc'] = 2
    with pytest.raises(ValidationError, match='LNC'):
        ServingSpec.model_validate(data)


@pytest.mark.parametrize('profile', ['vllm-neuron', 'pumice-neuron'])
def test_neuron_profiles_share_lnc_and_core_range_rules(spec, source, tmp_path, monkeypatch, profile):
    import types
    data = spec.model_dump(); data['runtime_profile'] = profile
    with pytest.raises(ValidationError, match='LNC'):
        ServingSpec.model_validate(data)
    data['lnc'] = 2
    data['workers'][0]['resource_ids'] = [0, 1]; data['workers'][1]['resource_ids'] = [2, 3]
    neuron = ServingSpec.model_validate(data)
    d = Deployment(neuron, arm_for(neuron, source), tmp_path/'neuron', deadline=time.monotonic()+10)
    env = d.environment(neuron.workers[1])
    assert (env['NEURON_LOGICAL_NC_CONFIG'], env['NEURON_VISIBLE_DEVICES'], env['CUDA_VISIBLE_DEVICES']) == ('2', '2-3', '')
    assert d.environment(None)['NEURON_LOGICAL_NC_CONFIG'] == '2'
    from deployment_ops import reserve_deployment
    writes = []
    fake = types.SimpleNamespace(_discover_session_resources=lambda: [0, 1, 2, 3],
                                 write_reservation=lambda *a, **k: writes.append(a))
    monkeypatch.setitem(sys.modules, 'gpu_reservation', fake)
    monkeypatch.setenv('AMMO_SESSION_ID', 'test-owner')
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE', profile)
    monkeypatch.setenv('NEURON_LOGICAL_NC_CONFIG', '1')
    with pytest.raises(ContractError, match='LNC differs'):
        with reserve_deployment(neuron, 'test-owner:pd:unique'):
            pytest.fail('LNC mismatch must not reserve')
    assert not writes
    data['workers'][0]['resource_ids'] = [0, 2]; data['workers'][1]['resource_ids'] = [3]
    with pytest.raises(ValidationError, match='contiguous'):
        ServingSpec.model_validate(data)


def test_observed_inactive_mechanism_can_fail_without_running_timing(spec,source,captured,monkeypatch):
    import run_serving_sweep as runner
    real=runner.run_adapter
    def inactive(name,deployment,directory,*args,**kwargs):
        result=real(name,deployment,directory,*args,**kwargs)
        if name=='activation':
            result['workers']['d']['activation_observed']=False
            write_json(directory/'result.json',result)
        return result
    monkeypatch.setattr(runner,'run_adapter',inactive)
    root,baseline=captured;output=root/'inactive'
    result=run_fake(spec,source,output,'candidate',baseline)
    assert result['status']=='FAIL' and result['failed_gate']=='5.3a'
    assert result['completed_gates']==['5.1a','5.1b','5.2']
    assert not (output/'timing').exists()
    assert verify_result(output,spec,baseline)==result


def test_missing_activation_observation_is_blocked_not_failed(spec,source,captured,monkeypatch):
    import run_serving_sweep as runner
    real=runner.run_adapter
    def missing(name,deployment,directory,*args,**kwargs):
        result=real(name,deployment,directory,*args,**kwargs)
        if name=='activation':
            result['workers']['d'].pop('activation_observed')
            write_json(directory/'result.json',result)
        return result
    monkeypatch.setattr(runner,'run_adapter',missing)
    root,baseline=captured;output=root/'unknown-activation'
    with pytest.raises(ContractError,match='missing or malformed'):
        run_fake(spec,source,output,'candidate',baseline)
    assert read_json(output/'failure.json')['status']=='BLOCKED'
    assert not (output/'seal.json').exists()
