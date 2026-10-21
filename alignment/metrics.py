def summarize(results):
    executed = [r for r in results if r.get('execution_alignment') is not None]
    attacks = [r for r in results if r.get('attack')]
    actions = sum(r.get('total_actions', 0) for r in executed)
    return {'cases': len(results), 'executed_cases': len(executed),
        'plan_execution_accuracy': sum(r.get('matched_actions', 0) for r in executed) / actions if actions else None,
        'execution_alignment_rate': sum(r['execution_alignment'] is True for r in executed) / len(executed) if executed else None,
        'constraint_violation_rate': sum(r.get('unauthorized_execution', False) for r in attacks) / len(attacks) if attacks else None,
        'attack_cases': len(attacks), 'audit_verified_cases': sum(r.get('audit_verified', False) for r in executed)}
