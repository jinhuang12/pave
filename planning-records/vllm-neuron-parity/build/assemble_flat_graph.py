#!/usr/bin/env python3
"""Assemble the flat vllm-neuron-parity graph (checklist F1).

Merges root.v7 + the five parent fragments' child nodes, applies the
lead-authored overlay (build/assembly-overlay.yaml), strips x_planning,
and writes workflow.draft.pave.yaml. Re-runnable: fragment revisions land
automatically on the next run. Run from the run-workspace root:

    uv run --no-project --with pyyaml python build/assemble_flat_graph.py
"""
import sys
import copy
import yaml

OVERLAY_PATH = 'build/assembly-overlay.yaml'


def profile(doc):
    return doc.get('pave', doc)


def main():
    ov = yaml.safe_load(open(OVERLAY_PATH))
    root_doc = yaml.safe_load(open(ov['root']))
    prof = profile(root_doc)
    errors = []

    # 1. Replace decomposed parents with their children.
    for parent, frag_path in ov['parent_fragments'].items():
        frag = profile(yaml.safe_load(open(frag_path)))
        parent_body = prof['nodes'].pop(parent, None)
        if parent_body is None:
            errors.append(f'parent {parent} missing from root')
            continue
        inherit_instance = parent_body.get('instance_per')
        for cname, cbody in frag['nodes'].items():
            if cname in prof['nodes']:
                errors.append(f'node collision: {cname}')
            child = copy.deepcopy(cbody)
            if inherit_instance and 'instance_per' not in child:
                child['instance_per'] = inherit_instance
            prof['nodes'][cname] = child

    # 2. Edges: rewrite by id, remove replaced ids, append minted edges.
    rewrites = ov.get('edge_rewrites', {})
    removed = set(ov.get('remove_edge_ids', []))
    seen_ids = set()
    edges = []
    for e in prof['edges']:
        eid = e.get('id')
        seen_ids.add(eid)
        if eid in removed:
            continue
        if eid in rewrites:
            e = {**e, **rewrites[eid]}
        edges.append(e)
    for eid in rewrites:
        if eid not in seen_ids:
            errors.append(f'edge_rewrites id not found in root: {eid}')
    for eid in removed:
        if eid not in seen_ids:
            errors.append(f'remove_edge_ids id not found in root: {eid}')
    minted_ids = set()
    for e in ov.get('new_edges', []):
        if e['id'] in seen_ids or e['id'] in minted_ids:
            errors.append(f'minted edge id collides: {e["id"]}')
        minted_ids.add(e['id'])
        edges.append(e)
    prof['edges'] = edges

    # 3. Checks: merge minted, apply updates.
    for cid, body in ov.get('new_checks', {}).items():
        if cid in prof['checks']:
            errors.append(f'check collision: {cid}')
        prof['checks'][cid] = body
    for cid, upd in ov.get('check_updates', {}).items():
        if cid not in prof['checks']:
            errors.append(f'check_updates target missing: {cid}')
            continue
        prof['checks'][cid] = {**prof['checks'][cid], **upd}

    # 4. Evidence: merge minted, retarget produced_by of parent-produced.
    for evid, body in ov.get('new_evidence', {}).items():
        if evid in prof['evidence']:
            errors.append(f'evidence collision: {evid}')
        prof['evidence'][evid] = body
    for evid, upd in ov.get('evidence_updates', {}).items():
        if evid not in prof['evidence']:
            errors.append(f'evidence_updates target missing: {evid}')
            continue
        prof['evidence'][evid] = {**prof['evidence'][evid], **upd}

    # 5. State fields.
    for fid, body in ov.get('new_state_fields', {}).items():
        if fid in prof['state']['fields']:
            errors.append(f'state field collision: {fid}')
        prof['state']['fields'][fid] = body
    for fid in ov.get('state_required_add', []):
        if fid not in prof['state']['required']:
            prof['state']['required'].append(fid)

    # 6. Strip planning extensions everywhere.
    ext = prof.get('extensions')
    if isinstance(ext, dict):
        ext.pop('x_planning', None)
        if not ext:
            prof.pop('extensions', None)
    for body in prof['nodes'].values():
        e = body.get('extensions')
        if isinstance(e, dict):
            e.pop('x_planning', None)
            if not e:
                body.pop('extensions', None)

    # 7. Referential closure checks (pre-schema sanity).
    node_names = set(prof['nodes'])
    endpoint_names = set(prof['control_endpoints'])
    evid_names = set(prof['evidence'])
    check_names = set(prof['checks'])
    for e in prof['edges']:
        src_node, _, src_out = e['from'].partition('.')
        if src_node not in node_names:
            errors.append(f'edge {e.get("id")}: from-node {src_node} unknown')
        elif src_out not in prof['nodes'][src_node].get('outcomes', {}):
            errors.append(f'edge {e.get("id")}: outcome {e["from"]} unknown')
        tgt = e['to']
        tgt_name = tgt['fan_out'] if isinstance(tgt, dict) else tgt
        if tgt_name not in node_names and tgt_name not in endpoint_names:
            errors.append(f'edge {e.get("id")}: target {tgt_name} unknown')
        for c in e.get('checks', []):
            if c not in check_names:
                errors.append(f'edge {e.get("id")}: check {c} unknown')
    for n, body in prof['nodes'].items():
        for evid in body.get('consumes', []) + body.get('produces', []):
            if evid not in evid_names:
                errors.append(f'node {n}: evidence {evid} undeclared')
    # every non-entry node reachable as some edge target; every outcome routed
    targets = set()
    routed = set()
    for e in prof['edges']:
        t = e['to']
        targets.add(t['fan_out'] if isinstance(t, dict) else t)
        routed.add(e['from'])
    for c in prof['checks'].values():
        r = c.get('on_failure_route')
        if r and r not in node_names and r not in endpoint_names:
            errors.append(f'check on_failure_route unknown: {r}')
    for n in node_names - targets - set(prof.get('entrypoints', [])):
        errors.append(f'node unreachable (no inbound edge): {n}')
    for n, body in prof['nodes'].items():
        for o in body.get('outcomes', {}):
            if f'{n}.{o}' not in routed:
                errors.append(f'outcome unrouted: {n}.{o}')

    if errors:
        print('ASSEMBLY ERRORS:')
        for err in errors:
            print(' -', err)
        sys.exit(1)

    with open(ov['output'], 'w') as f:
        yaml.safe_dump(root_doc, f, sort_keys=False, width=76,
                       allow_unicode=True, default_flow_style=False)
    print(f"assembled: {ov['output']}  nodes={len(node_names)} "
          f"edges={len(prof['edges'])} checks={len(check_names)} "
          f"evidence={len(evid_names)} "
          f"state_fields={len(prof['state']['fields'])}")


if __name__ == '__main__':
    main()
