"""Conservative independent graph equivalence, separate from frozen Gold.

Ignores IDs, alias spelling and the ordering of independent nodes. Includes all
operations, parameter provenance and transitive causal edges; never ignores an
extra payment, missing goal or unrelated read. Both graphs must first validate.
"""
from collections import Counter
from llm.hashing import snapshot
from runtime.task_graph import TaskGraph


def signature(plan, specs):
    labels = {}
    ancestors = {}
    kinds = {}
    # Frozen WealthAgent.recommend reads authoritative profile/catalog itself.
    # Redundant zero-parameter preparatory reads can be contracted ONLY when
    # their sole consumers are that intrinsic operation or other preparatory
    # reads. Explicit terminal/report outputs and value consumers stay visible.
    rows=TaskGraph.from_plan(plan).topological_sort()
    intrinsic={'customer.get_risk_profile','wealth.list_products'}
    preparation={n.node_id for n in rows if n.agent=='wealth' and not n.parameters
        and specs[(n.agent,n.action)].permission_action in intrinsic}
    delegates={n.node_id for n in rows if n.agent=='wealth' and n.action=='wealth.recommend'}
    used={ref[:-7] for n in rows for ref in n.parameters.values() if ref.endswith(':result')}
    children={n.node_id:{c.node_id for c in rows if n.node_id in c.dependency} for n in rows}
    drop=preparation-used
    while True:
        narrowed={key for key in drop if children[key] and children[key]<=drop|delegates}
        if narrowed==drop: break
        drop=narrowed
    for node in rows:
        spec = specs[(node.agent, node.action)]
        kinds[node.node_id] = spec.node_type
        parents = set(node.dependency)
        ancestors[node.node_id] = parents | set().union(*(ancestors[p] for p in parents))
        params = {key: ('input', ref) if ref.startswith('input:') else
                  ('result', labels[ref[:-7]]) for key, ref in node.parameters.items()}
        labels[node.node_id] = snapshot([spec.agent, spec.permission_action, params])
    return Counter(snapshot([labels[n.node_id], sorted(labels[a] for a in ancestors[n.node_id]
        if a not in drop and not (kinds[n.node_id] == kinds[a] == 'READ'))])
                   for n in plan.steps if n.node_id not in drop)


def equivalent(actual, expected, context, validator):
    validator.validate(actual, context)
    validator.validate(expected, context)
    specs = validator.specifications()
    return signature(actual, specs) == signature(expected, specs)
