"""CPU-only state-machine tests. Synthetic audits/results are NOT approvals."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

# Reuse the explicitly synthetic subprocess driver, not model inference.
from test_ammo_serving import (ROOT, source, spec, captured, run_fake, arm_for,
                              fake_reserve, FakeDeployment, fake_correctness)
from serving_contract import Arm, ContractError, ServingSpec, digest, read_json, write_json
from serving_evidence import promote, seal, verify_result
from serving_campaign import (binding, synchronize, violation, file_ref, evidence_ref,
    mining_result, current_round, validation_report, write_track_report, round_baseline)
from run_serving_sweep import execute

STAMP = '2026-09-23T18:00:00+00:00'


def module_at(name, path):
    desc = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(desc)
    sys.modules[name] = module
    desc.loader.exec_module(module)
    return module


@pytest.fixture(params=['claude', 'codex'])
def engine(request, monkeypatch):
    scripts = ROOT / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    module = module_at('serving_state_' + request.param, scripts/'ammo_state.py')
    monkeypatch.setitem(sys.modules, 'ammo_state', module)
    target = module_at('serving_target_' + request.param, scripts/'new_target.py')
    schema = read_json(ROOT / 'schemas/state.schema.json')
    return module, target, schema, request.param


@pytest.fixture
def campaign(engine, spec, captured, monkeypatch):
    module, target, schema, variant = engine
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1')
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE', 'vllm-cuda')
    root = captured[0].parent
    fields = target.TargetFields(model_id=spec.model_id, hardware='CPU_TEST_ONLY', dtype=spec.dtype,
        tp=1, ep=1, max_model_len=4096, input_len=10, output_len=3, batch_sizes=[1], num_iters=6,
        noise_tolerance_pct=.5, catastrophic_regression_pct=5.)
    state = target._state_json(fields, root, gpu_model='CPU_TEST_ONLY')
    state['serving'] = binding(spec)
    state['campaign']['rounds'][0]['serving'] = {}
    write_json(root/'target.json', {'target':state['target'], 'serving':spec.model_dump()})
    synchronize(state, root, 'baseline', captured[1])
    write_json(root/'state.json', state)
    assert_valid(module, schema, state, root)
    return state, root, captured[1]


def assert_valid(module, schema, state, root, previous=None):
    errors = list(Draft202012Validator(schema).iter_errors(state))
    assert not errors, [(list(e.path), e.message) for e in errors]
    assert module.gate_violation(state, module.load_transitions(), previous, root) is None


def audit(state, key):
    # These timestamps are intentionally fabricated unit-test inputs to exercise
    # the guard. They do not represent an agent review or authorize production.
    current_round(state)['audit'][key] = {'started_at':STAMP, 'passed_at':STAMP, 'cycle':1}


def enter_mining(state, root, baseline, spec, top=20., complete=True, filename='mining.json'):
    state['campaign']['current_stage'] = '2_bottleneck_mining'
    audit(state,'stage_1')
    evidence = root/'profile-fixture.txt'
    evidence.write_text('SYNTHETIC CPU EVIDENCE; NOT A PROFILE OR A PERFORMANCE CLAIM\n')
    raw = {'schema_version':'ammo_serving_mining/1', 'contract_sha256':spec.checksum,
        'incumbent_sha256':digest(read_json(baseline/'seal.json')), 'objective':spec.policy.objective,
        'coverage_complete':complete, 'workload_ids':[w.id for w in spec.workloads],
        'opportunities':[{'id':role, 'role':role, 'scope':'bound', 'upper_bound_improvement_pct':top,
                         'evidence':[file_ref(root,evidence)], 'derivation':'synthetic bound for a state-machine test'}
                        for role in ['prefill','decode','handoff','router']]}
    path=root/filename;write_json(path,raw)
    synchronize(state,root,'mining',path)
    return path


def enter_tracks(state, root, spec):
    audit(state,'stage_2')
    rnd=current_round(state)
    rnd['debate']['selected_winners']=['OP-001','OP-002']
    candidates=[]
    for op in rnd['debate']['selected_winners']:
        proposal=root/'rounds'/'1'/'debate'/'proposals'/(op+'.md')
        proposal.parent.mkdir(parents=True,exist_ok=True)
        proposal.write_text('SYNTHETIC TEST PROPOSAL; no performance claim.\n')
        candidates.append({'op_id':op,'track_assignment':'lossless','proposal_file':str(proposal.relative_to(root)),
             'category':'kernel_replacement','selection_mode':'ordinary',
             'score_breakdown':{'feasibility':8.,'evidence_tier':'tier_1','expected_objective_pct':10.,
                                'weighted_total':8.,'evidence_scope':'bound'},
             'stage_4_validation_obligations':['activation_range_check'],
             'cited_evidence':['profile-fixture.txt'],
             'serving_projection_evidence':[file_ref(root,root/'profile-fixture.txt')]})
        directory=root/'rounds'/'1'/'tracks'/op;directory.mkdir(parents=True,exist_ok=True)
        meta={'status':'IN_PROGRESS','implementer_agent':'impl-'+op,'implementer_rollout_id':'rollout-'+op,
              'monitor_agent':'monitor-'+op}
        for field,name in [('monitor_evidence_path','observations.txt'),('monitor_offsets_path','offsets.json'),('monitor_summary_path','monitor.json')]:
            meta[field]=str((directory/name).relative_to(root))
        (directory/'observations.txt').write_text('Synthetic monitor fixture; not an independent review.\n')
        common={'target_agent':meta['implementer_agent'],'target_rollout_id':meta['implementer_rollout_id']}
        write_json(directory/'offsets.json',{**common,'last_offset':0})
        write_json(directory/'monitor.json',{**common,'monitor_agent':meta['monitor_agent'],'coverage_status':'TRACK_COMPLETE'})
        rnd['parallel_tracks']['tracks'][op]=meta
    rnd['debate']['selected_candidates']=candidates
    state['campaign']['current_stage']='4_5_parallel_tracks'


def finish_tracks(state,root,baseline,spec,source):
    for op in ['OP-001','OP-002']:
        path=root/'serving'/'rounds'/'1'/'candidate'/op
        run_fake(spec,source,path,'candidate',baseline)
        synchronize(state,root,'track',path,op)


def test_serving_baseline_does_not_waive_stage1_audit(engine,campaign):
    module,_,schema,_=engine;state,root,_=campaign
    previous=copy.deepcopy(state);state['campaign']['current_stage']='2_bottleneck_mining'
    reason=module.gate_violation(state,module.load_transitions(),previous,root)
    assert 'stage_1' in reason
    audit(state,'stage_1');assert_valid(module,schema,state,root,previous)


def test_schema_keeps_batch_and_serving_score_units_distinct(engine,campaign,spec):
    _,_,schema,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    assert not list(Draft202012Validator(schema).iter_errors(state))
    score=current_round(state)['debate']['selected_candidates'][0]['score_breakdown']
    score['expected_e2e_pct']=score.pop('expected_objective_pct')
    assert list(Draft202012Validator(schema).iter_errors(state))
    assert 'batch E2E' in violation(state,root)


def test_missing_serving_binding_cannot_fall_through_to_batch(engine,campaign):
    module,_,_,_=engine;state,root,_=campaign
    del state['serving']
    assert 'binding differs' in module.gate_violation(state,module.load_transitions(),artifact_dir=root)


def test_state_validation_requires_evidence_directory(engine,campaign):
    module,_,_,_=engine;state,_,_=campaign
    assert 'artifact directory' in module.gate_violation(state,module.load_transitions())


@pytest.mark.parametrize('mutation',[
    lambda x:x.update(coverage_complete=False),
    lambda x:x['opportunities'][0].update(upper_bound_improvement_pct=None),
    lambda x:x['opportunities'].pop(),
])
def test_unknown_or_partial_mining_is_not_zero_opportunity(engine,campaign,spec,mutation):
    _,_,_,_=engine;state,root,base=campaign
    path=enter_mining(state,root,base,spec,top=.01)
    raw=read_json(path);mutation(raw);write_json(path,raw)
    result=mining_result(file_ref(root,path),root,spec,base)
    assert result['decision']=='UNRESOLVED' and result['top_addressable_objective_pct'] is None


def test_changed_or_stale_mining_is_not_accepted(engine,campaign,spec):
    _,_,_,_=engine;state,root,base=campaign
    path=enter_mining(state,root,base,spec)
    raw=read_json(path);raw['incumbent_sha256']='f'*64;write_json(path,raw)
    with pytest.raises(ContractError,match='stale'):mining_result(file_ref(root,path),root,spec,base)


def test_all_old_cohort_and_stage45_audits_still_bind(engine,campaign,spec,source):
    module,_,schema,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    finish_tracks(state,root,base,spec,source)
    assert_valid(module,schema,state,root)
    prev=copy.deepcopy(state);state['campaign']['current_stage']='6_integration'
    assert 'stage_45' in module.gate_violation(state,module.load_transitions(),prev,root)
    audit(state,'stage_45');assert_valid(module,schema,state,root,prev)
    del current_round(state)['parallel_tracks']['tracks']['OP-002']
    assert 'match exactly' in module.gate_violation(state,module.load_transitions(),artifact_dir=root)


def test_verified_measurement_cannot_claim_a_different_track_verdict(engine,campaign,spec,source):
    module,_,_,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    finish_tracks(state,root,base,spec,source)
    current_round(state)['parallel_tracks']['tracks']['OP-001']['status']='GATED_PASS'
    assert 'verdict differs' in module.gate_violation(state,module.load_transitions(),artifact_dir=root)


def test_terminal_failure_needs_repair_and_fallback_closure(engine,campaign,spec,source):
    module,_,schema,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    def wrong(deployment,spec):
        value=fake_correctness(deployment,spec);value['answers']=['0']*spec.policy.correctness_questions;return value
    path=root/'serving'/'failure';run_fake(spec,source,path,'candidate',base,wrong)
    with pytest.raises(ContractError,match='fallback disposition'):
        synchronize(state,root,'track',path,'OP-001')
    repairs=root/'repairs.txt';repairs.write_text('Synthetic attempted repair log; not production evidence.\n')
    closure=root/'failure-closure.json'
    write_json(closure,{'op_id':'OP-001','result_sha256':digest(read_json(path/'seal.json')),
        'attempted_repairs':[file_ref(root,repairs)],'fallback_disposition':'synthetic no-safe-fallback fixture',
        'unresolved_actions':[],'exhausted':True})
    synchronize(state,root,'failure-closure',closure,'OP-001')
    synchronize(state,root,'track',path,'OP-001')
    assert current_round(state)['parallel_tracks']['tracks']['OP-001']['status']=='FAIL'
    assert_valid(module,schema,state,root)


def test_missing_monitor_cannot_be_replaced_by_serving_metrics(engine,campaign,spec,source):
    _,_,_,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    finish_tracks(state,root,base,spec,source)
    track=current_round(state)['parallel_tracks']['tracks']['OP-001']
    (root/track['monitor_summary_path']).unlink()
    report=validation_report(state,root,'OP-001')
    assert report.blockers and report.advance_to_stage6 is False


def test_serving_report_recomputes_instead_of_trusting_cache(engine,campaign,spec,source):
    _,_,_,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    finish_tracks(state,root,base,spec,source)
    ref=current_round(state)['parallel_tracks']['tracks']['OP-001']['serving_result']
    write_json(root/ref['path']/'summary.json',{'status':'FAIL'})
    result=write_track_report(state,root,'OP-001')
    text=(root/'rounds/1/tracks/OP-001/validation_results.md').read_text()
    assert result['status']=='PASS' and 'fixed offered load' in text and 'kernel speedups' in text


def test_full_serving_round_uses_existing_audits_and_remines_promoted_incumbent(engine,campaign,spec,source):
    module,_,schema,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    finish_tracks(state,root,base,spec,source)
    audit(state,'stage_45');state['campaign']['current_stage']='6_integration'
    rnd=current_round(state);rnd['integration']['selected_candidates']=['OP-001','OP-002']
    composition={'selected_results':{op:rnd['parallel_tracks']['tracks'][op]['serving_result']['sha256'] for op in ['OP-001','OP-002']}}
    integrated=root/'serving'/'rounds'/'1'/'integration'/'combined'
    execute(spec,arm_for(spec,source,'integration'),integrated,'integration',incumbent=base,
        reserve=fake_reserve,deployment_class=FakeDeployment,correctness=fake_correctness,composition=composition)
    synchronize(state,root,'integration',integrated)
    assert_valid(module,schema,state,root)
    pointer=root/'serving'/'incumbent.json'
    promote(pointer,base,spec,expected=None)
    promote(pointer,integrated,spec,expected=digest(read_json(base/'seal.json')))
    rnd['shipped']=['OP-001','OP-002'];rnd['status']='SHIPPED'
    audit(state,'stage_67')
    prev=copy.deepcopy(state);state['campaign']['current_stage']='7_campaign_eval'
    assert_valid(module,schema,state,root,prev)
    state['campaign']['status']='campaign_complete'
    # Even valid old bounds cannot stop a campaign after a material SHIP.
    assert module.gate_violation(state,module.load_transitions(),artifact_dir=root)
    state['campaign']['status']='active'
    prev=copy.deepcopy(state);module.advance(state,'SHIP',module.load_transitions())
    assert_valid(module,schema,state,root,prev)
    assert round_baseline(state,root,spec)==integrated
    assert 'mining' not in current_round(state)['serving']
    # A fresh low bound on the new baseline permits the existing post-SHIP edge.
    enter_mining(state,root,integrated,spec,top=.1,filename='round2-mining.json')
    audit(state,'stage_2')  # Fresh incumbent mining still needs its independent audit.
    prev=copy.deepcopy(state);state['campaign']['current_stage']='7_campaign_eval'
    state['campaign']['status']='campaign_complete'
    assert_valid(module,schema,state,root,prev)
    recorded=state['campaign']['rounds'][0]['serving']['accounting']
    assert recorded['method'].startswith('direct original-to-current')
    assert recorded['workloads']['w1']['improvement_pct']==pytest.approx(20.)
    assert state['campaign']['rounds'][0]['combined_e2e_speedup_x'] is None


def test_batch_cli_overrides_are_rejected_by_serving_scaffold(engine,spec,tmp_path,monkeypatch):
    _,_,_,variant=engine;manifest=tmp_path/'manifest.json';write_json(manifest,spec.model_dump())
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0,1')
    scripts=ROOT/'scripts'
    result=subprocess.run([sys.executable,str(scripts/'new_target.py'),'--artifact-dir',str(tmp_path/'new'),
        '--serving-manifest',str(manifest),'--input-len','99'],text=True,capture_output=True)
    assert result.returncode!=0 and 'batch workload/gate flags' in result.stderr
    assert not (tmp_path/'new/state.json').exists()


def test_serving_scaffold_selects_only_serving_measurement(engine,spec,tmp_path,monkeypatch):
    _,_,_,variant=engine;manifest=tmp_path/'manifest.json';write_json(manifest,spec.model_dump())
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','0,1')
    scripts=ROOT/'scripts'
    result=subprocess.run([sys.executable,str(scripts/'new_target.py'),'--artifact-dir',str(tmp_path/'new'),
        '--serving-manifest',str(manifest),'--hardware','CPU_TEST_ONLY','--gpu-model','CPU_TEST_ONLY'],text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    state=read_json(tmp_path/'new/state.json');target=read_json(tmp_path/'new/target.json')
    assert state['serving']==binding(spec) and 'bench' not in target and 'workload' not in target


def test_terminal_hint_only_fires_on_a_real_edge(engine,campaign):
    module,_,schema,_=engine;state,root,_=campaign
    state['campaign']['status']='campaign_complete'
    assert module.compute_next_step(state,None,module.load_transitions(),schema)[1] is False
    assert module.compute_next_step(state,copy.deepcopy(state),module.load_transitions(),schema)[1] is False
    previous=copy.deepcopy(state);previous['campaign']['status']='active'
    assert module.compute_next_step(state,previous,module.load_transitions(),schema)[1] is True


def test_serving_sync_failed_validation_leaves_state_bytes_unchanged(engine,campaign):
    from types import SimpleNamespace
    module,_,schema,_=engine;state,root,base=campaign
    state['campaign']['current_stage']='2_bottleneck_mining'
    write_json(root/'state.json',state)
    original=(root/'state.json').read_bytes()
    args=SimpleNamespace(state=str(root/'state.json'),kind='baseline',from_=str(base),op_id=None,schema=None)
    assert module.cmd_serving_sync(args)==1
    assert (root/'state.json').read_bytes()==original


def test_objective_gain_cannot_be_labeled_as_batch_latency(engine,campaign):
    module,_,_,_=engine;state,root,_=campaign
    current_round(state)['combined_e2e_speedup_x']=1.2
    assert 'batch E2E accounting' in module.gate_violation(state,module.load_transitions(),artifact_dir=root)


@pytest.mark.parametrize('expected',[0.1,101.])
def test_serving_projection_respects_its_own_floor_and_units(engine,campaign,spec,expected):
    module,_,_,_=engine;state,root,base=campaign
    enter_mining(state,root,base,spec);enter_tracks(state,root,spec)
    candidate=current_round(state)['debate']['selected_candidates'][0]
    candidate['score_breakdown'].update(expected_objective_pct=expected,weighted_total=0.8*expected)
    reason=module.gate_violation(state,module.load_transitions(),artifact_dir=root)
    assert 'floor' in reason or '100 percent' in reason
