"""Public-only round evidence verification. Trust pins are caller supplied.

This proves supplied evidence integrity and deterministic recomputation, not
WeID finality, receipt completeness, model inference or financial authorization.
"""
from backend.domain import Rejected, digest
from backend.audit_trust import merkle
from backend.audit_trust.records import verify_record
from backend.security.crypto import ALGORITHM, PROFILE, verify_object
from backend.security.message_v3 import FIELDS, MESSAGE_DOMAIN
from backend.collaboration.rounds import VERSION, AGGREGATION, PAYLOAD_FIELDS, aggregate, CollaborationRounds


def verify_round(bundle, *, pinned_snapshot, pinned_audit_key, expected_record_hash):
    try:
        record, evidence = bundle['record'], bundle['evidence']
        if (digest(record) != expected_record_hash or record['version'] != VERSION
                or record['aggregation_version'] != AGGREGATION
                or record['tree_version'] != 'TRUSTA-RFC6962-V1'
                or record['financial_authority'] is not False
                or digest(pinned_snapshot) != record['snapshot_hash'] or bundle['snapshot'] != pinned_snapshot
                or record['policy_id'] != pinned_snapshot['policy_id']
                or type(record['evidence_count']) is not int or record['evidence_count'] != len(evidence)
                or type(record['k']) is not int or not 1 <= record['k'] <= len(pinned_snapshot['members']) <= 6
                or type(record['deadline_ns']) is not int or not 0 < record['deadline_ns'] <= 300_000_000_000):
            return False
        keys = []; seen = set(); seqs = set(); nonces = set()
        for item in evidence:
            message, payload = item['message'], item['payload']
            member = pinned_snapshot['members'][message['sender_agent']]
            CollaborationRounds._assessment(payload['assessment'])
            if (set(message) != FIELDS or set(payload) != PAYLOAD_FIELDS or message['schema_version'] != 3
                    or message['canonicalization'] != PROFILE or message['signature_algorithm'] != ALGORITHM
                    or message['task_id'] != record['task_id'] or message['step_id'] != 'anchor-round:' + record['round_id']
                    or message['receiver_agent'] != 'supervisor' or message['message_type'] != 'READ_ONLY_REPORT'
                    or message['sender_key_id'] != member['key_id']
                    or message['payload_version'] != record['task_revision']
                    or message['payload_ref'] != 'anchor:'+record['round_id']+':'+message['sender_agent']
                    or message['capability_snapshot_hash'] != member['capability_hash']
                    or message['payload_hash'] != digest(payload) or message['nonce'] in nonces
                    or payload['version'] != VERSION or payload['round_id'] != record['round_id']
                    or payload['snapshot_hash'] != record['snapshot_hash']
                    or payload['policy_id'] != record['policy_id'] or payload['epoch'] != member['epoch']
                    or payload['did'] != member['did'] or payload['model_commitment'] != member['model_commitment']
                    or payload['did'] in seen or type(item['accepted_ns']) is not int
                    or not 0 <= item['accepted_ns'] <= record['deadline_ns']
                    or type(item['receipt_seq']) is not int or not 1 <= item['receipt_seq'] <= len(evidence)
                    or item['receipt_seq'] in seqs):
                return False
            cap=payload['assessment']['capability']
            if ((cap=='finance.summary' and message['sender_agent']!='finance')
                    or (cap=='wealth.recommend' and message['sender_agent']!='wealth')
                    or (cap=='account.get_balance' and message['sender_agent'] not in {'finance','transaction'})):
                return False
            verify_object(member['public_key'], MESSAGE_DOMAIN,
                          {k: v for k, v in message.items() if k != 'signature'}, message['signature'])
            seen.add(payload['did']); nonces.add(message['nonce']); seqs.add(item['receipt_seq'])
            keys.append((payload['did'], message['payload_hash']))
        if keys != sorted(keys):
            return False
        hashes = [digest(e) for e in evidence]
        if merkle.root(hashes) != record['evidence_root']:
            return False
        if len(bundle['inclusion_proofs']) != len(evidence):
            return False
        for i, path in enumerate(bundle['inclusion_proofs']):
            if not merkle.verify_inclusion_proof(hashes[i], i, len(hashes), path, record['evidence_root']):
                return False
        if record['status'] == 'COMPLETED':
            if record['closure_reason'] != 'DEADLINE' or len(evidence) < record['k'] or record['verdict'] != aggregate(evidence):
                return False
        elif record['status'] == 'VOID':
            if (record['verdict'] is not None or record['closure_reason'] not in {'DEADLINE','COORDINATOR_REBOOT'}
                    or record['closure_reason']=='DEADLINE' and len(evidence)>=record['k']):
                return False
        else:
            return False
        proof = bundle['audit_proof']
        return bool(verify_record(proof, pinned_audit_key)
                    and proof['event']['payload_hash'] == digest(record)
                    and proof['event']['event_type'] == 'ANCHOR_ROUND_CLOSED'
                    and proof['event']['task_id'] == record['task_id']
                    and proof['audit_event_id'] == bundle['audit_event_id'])
    except (Rejected, KeyError, ValueError, TypeError, RecursionError):
        return False
