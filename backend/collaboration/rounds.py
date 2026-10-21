"""Full-window coordinator layered on the original V3, nonce and audit boundaries.

This versioned TRUSTA P-256 adaptation is not byte-compatible with the research
secp256k1 envelope. Identity/admission provisioning stays outside this collector.
Financial writes and user authorization have no entry point here.
"""
import hashlib
import hmac
import json
import time
from backend.domain import Rejected, canonical, digest, uid
from backend.audit_trust import merkle
from backend.security.message_v3 import TrustedMessageV3
from backend.security.crypto import HEX
from backend.collaboration.otc import integer


VERSION = 'TRUSTA-ANCHOR-P256-V1'
AGGREGATION = 'TRUSTA-BANK-READONLY-CROSSCHECK-V1'
PAYLOAD_FIELDS = frozenset({'version', 'round_id', 'snapshot_hash', 'policy_id',
                           'epoch', 'did', 'model_commitment', 'assessment'})
SCHEMA = '''
CREATE TABLE IF NOT EXISTS collaboration_rounds(id TEXT PRIMARY KEY, owner TEXT NOT NULL,
 task_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collaboration_outputs(round_id TEXT NOT NULL, agent TEXT NOT NULL,
 receipt_seq INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(round_id,agent),
 UNIQUE(round_id,receipt_seq));
CREATE TABLE IF NOT EXISTS collaboration_conflicts(id TEXT PRIMARY KEY,round_id TEXT NOT NULL,
 body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collaboration_closed(round_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collaboration_outbox(round_id TEXT PRIMARY KEY, status TEXT NOT NULL,
 record_hash TEXT NOT NULL, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS collaboration_output_immutable BEFORE UPDATE ON collaboration_outputs
 BEGIN SELECT RAISE(ABORT,'immutable collaboration output'); END;
CREATE TRIGGER IF NOT EXISTS collaboration_output_delete BEFORE DELETE ON collaboration_outputs
 BEGIN SELECT RAISE(ABORT,'immutable collaboration output'); END;
CREATE TRIGGER IF NOT EXISTS collaboration_closed_immutable BEFORE UPDATE ON collaboration_closed
 BEGIN SELECT RAISE(ABORT,'immutable closed round'); END;
CREATE TRIGGER IF NOT EXISTS collaboration_closed_delete BEFORE DELETE ON collaboration_closed
 BEGIN SELECT RAISE(ABORT,'immutable closed round'); END;
'''


def aggregate(evidence):
    """Pure, versioned conservative crosscheck; no banking authority or majority payment."""
    claims = [e['payload']['assessment'] for e in evidence]
    if not claims:
        raise Rejected('ANCHOR_EMPTY_AGGREGATION')
    if any(c['verdict'] == 'REVIEW_REQUIRED' for c in claims):
        return 'REVIEW_REQUIRED'
    if len({c['facts_hash'] for c in claims}) != 1:
        return 'REVIEW_REQUIRED'
    return 'CONSISTENT_READONLY_EVIDENCE'


class CollaborationRounds:
    def __init__(self, service, admission, *, clock=time.monotonic_ns, boot_id=None):
        if not isinstance(service.messages, TrustedMessageV3):
            raise Rejected('ANCHOR_REQUIRES_V3')
        self.s, self.admission, self.clock = service, admission, clock
        if boot_id is None:
            import psutil
            boot_id = str(int(psutil.boot_time()))
        self.boot_id = boot_id
        self.seal_key = hmac.new(service.config.signing_key,
                                b'TRUSTA-ANCHOR-ROUND-STATE-V1', hashlib.sha256).digest()
        conn = service.db.connect()
        try:
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _encode(self, body):
        return canonical({'body': body, 'seal': hmac.new(self.seal_key, canonical(body).encode(), hashlib.sha256).hexdigest()})

    def _decode(self, row):
        try:
            wrapper = json.loads(row['body'])
            body = wrapper['body']
            expected = hmac.new(self.seal_key, canonical(body).encode(), hashlib.sha256).hexdigest()
            if set(wrapper) != {'body', 'seal'} or not hmac.compare_digest(expected, wrapper['seal']):
                raise ValueError()
            return body
        except (ValueError, KeyError, TypeError):
            raise Rejected('ANCHOR_STATE_TAMPERED') from None

    def _round(self, conn, round_id, owner):
        row = conn.execute('SELECT * FROM collaboration_rounds WHERE id=?', (round_id,)).fetchone()
        if not row or row['owner'] != owner:
            raise Rejected('ANCHOR_ROUND_ACCESS_DENIED')
        body = self._decode(row)
        if body['round_id'] != round_id or body['owner'] != owner or body['task_id'] != row['task_id']:
            raise Rejected('ANCHOR_STATE_TAMPERED')
        return body

    def start(self, task_id, owner, agents, *, k, deadline, policy_id, model_commitments):
        task = self.s._owned_task(task_id, owner)
        integer(k, 1)
        duration = integer(deadline['deadline_ns'], 1)
        horizon=integer(deadline.get('telemetry_horizon_ns',duration),1)
        if (not 1 <= k <= len(agents) <= 6 or len(set(agents)) != len(agents)
                or not duration<=horizon<=300_000_000_000 or not HEX.fullmatch(policy_id)):
            raise Rejected('ANCHOR_INVALID_ROUND_CONFIGURATION')
        # Mandatory real verification belongs to this injected trusted adapter.
        # An unavailable formal identity/proof service MUST raise, not downgrade.
        snapshot = self.admission.snapshot(agents, policy_id, model_commitments)
        if set(snapshot['members']) != set(agents):
            raise Rejected('ANCHOR_MEMBERSHIP_MISMATCH')
        for agent, member in snapshot['members'].items():
            identity = self.s.crypto_registry.resolve(agent)
            if (member['key_id'] != identity['key_id']
                    or member['capability_hash'] != identity['capability_hash']
                    or member['public_key'] != identity['public_key']
                    or member['model_commitment'] != model_commitments[agent]
                    or type(member['epoch']) is not int or member['epoch'] < 0):
                raise Rejected('ANCHOR_IDENTITY_ADMISSION_BINDING_MISMATCH')
        state = {'version': VERSION, 'round_id': uid(), 'owner': owner, 'task_id': task_id,
                 'task_revision': task['revision'], 'snapshot': snapshot,
                 'snapshot_hash': digest(snapshot), 'policy_id': policy_id, 'k': k,
                 'duration_ns': duration, 'started_ns': self.clock(), 'boot_id': self.boot_id,
                 'telemetry_horizon_ns':horizon,
                 'started_at_ms': int(time.time()*1000),
                 'deadline_selection': deadline, 'status': 'COLLECTING',
                 'aggregation_version': AGGREGATION, 'financial_authority': False}
        with self.s.lock, self.s.db.transaction() as conn:
            conn.execute('INSERT INTO collaboration_rounds VALUES(?,?,?,?)',
                         (state['round_id'], owner, task_id, self._encode(state)))
            self.s.audit.append_in(conn, task_id, None, 'ANCHOR_ROUND_STARTED', 'supervisor', state)
        return state

    def sign(self, round_id, owner, agent, assessment):
        with self.s.lock, self.s.db.transaction() as conn:
            state = self._round(conn, round_id, owner)
        member = state['snapshot']['members'].get(agent)
        if not member:
            raise Rejected('ANCHOR_NOT_ADMITTED')
        payload = {'version': VERSION, 'round_id': round_id,
                   'snapshot_hash': state['snapshot_hash'], 'policy_id': state['policy_id'],
                   'epoch': member['epoch'], 'did': member['did'],
                   'model_commitment': member['model_commitment'], 'assessment': assessment}
        self._assessment(assessment)
        message = self.s.messages.sign(agent, 'supervisor', state['task_id'],
                                       'anchor-round:' + round_id, 'READ_ONLY_REPORT',
                                       'anchor:' + round_id + ':' + agent, payload,
                                       payload_version=state['task_revision'])
        return {'message': message, 'payload': payload}

    @staticmethod
    def _assessment(assessment):
        if (not isinstance(assessment, dict)
                or set(assessment) != {'verdict', 'facts_hash', 'result_hash', 'capability'}
                or not isinstance(assessment['verdict'], str)
                or not isinstance(assessment['capability'], str)
                or assessment['verdict'] not in {'CONSISTENT', 'REVIEW_REQUIRED'}
                or assessment['capability'] not in {'finance.summary', 'wealth.recommend', 'account.get_balance'}
                or any(not isinstance(assessment[n], str) or not HEX.fullmatch(assessment[n])
                       for n in ('facts_hash', 'result_hash'))):
            raise Rejected('ANCHOR_INVALID_READONLY_ASSESSMENT')

    def accept(self, round_id, owner, bundle):
        if not isinstance(bundle, dict) or set(bundle) != {'message', 'payload'}:
            raise Rejected('ANCHOR_INVALID_OUTPUT')
        message, payload = bundle['message'], bundle['payload']
        if not isinstance(message, dict) or not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
            raise Rejected('ANCHOR_INVALID_OUTPUT')
        try:
            if len(canonical(payload).encode('utf-8')) > 4096:
                raise Rejected('ANCHOR_OUTPUT_TOO_LARGE')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise Rejected('ANCHOR_INVALID_OUTPUT') from None
        self._assessment(payload['assessment'])
        # Network identity checks occur before opening the SQLite write transaction.
        # Local revocation is checked again atomically below; unavailable identity
        # services fail closed. No long-lived chain-status cache is accepted.
        with self.s.lock, self.s.db.transaction() as conn:
            before = self._round(conn, round_id, owner)
            # Reject malformed/forged messages before spawning any SDK process.
            self.s.messages.verify_in(message,payload,'supervisor',conn)
        candidate = before['snapshot']['members'].get(message.get('sender_agent'))
        if not candidate:
            raise Rejected('ANCHOR_NOT_ADMITTED')
        self.admission.check_current(message['sender_agent'],candidate,before['policy_id'])
        with self.s.lock, self.s.db.transaction() as conn:
            state = self._round(conn, round_id, owner)
            self.s.messages.verify_in(message, payload, 'supervisor', conn)
            member = state['snapshot']['members'].get(message['sender_agent'])
            if not member:
                raise Rejected('ANCHOR_NOT_ADMITTED')
            self.admission.check_current_in(conn,message['sender_agent'],member,state['policy_id'])
            capability = payload['assessment']['capability']
            if capability == 'finance.summary' and message['sender_agent'] != 'finance':
                raise Rejected('ANCHOR_ASSESSMENT_CAPABILITY_DENIED')
            if capability == 'wealth.recommend' and message['sender_agent'] != 'wealth':
                raise Rejected('ANCHOR_ASSESSMENT_CAPABILITY_DENIED')
            if capability == 'account.get_balance':
                self.s.registry.check_bank(message['sender_agent'], 'balance.read')
            if (payload['version'] != VERSION or payload['round_id'] != round_id
                    or payload['snapshot_hash'] != state['snapshot_hash']
                    or payload['policy_id'] != state['policy_id']
                    or type(payload['epoch']) is not int or payload['epoch'] != member['epoch']
                    or payload['did'] != member['did']
                    or payload['model_commitment'] != member['model_commitment']
                    or message['task_id'] != state['task_id']
                    or message['step_id'] != 'anchor-round:' + round_id
                    or message['payload_ref'] != 'anchor:' + round_id + ':' + message['sender_agent']
                    or message['message_type'] != 'READ_ONLY_REPORT'
                    or message['payload_version'] != state['task_revision']
                    or message['sender_key_id'] != member['key_id']
                    or message['capability_snapshot_hash'] != member['capability_hash']):
                raise Rejected('ANCHOR_CONTEXT_MISMATCH')
            row = conn.execute('SELECT * FROM collaboration_outputs WHERE round_id=? AND agent=?',
                               (round_id, message['sender_agent'])).fetchone()
            if row:
                prior = self._decode(row)
                if prior['message'] == message and prior['payload'] == payload:
                    return {'status': 'DUPLICATE', 'receipt_seq': prior['receipt_seq']}
                conflict = {'message': message, 'payload': payload, 'round_id': round_id}
                conflict_id=digest({'round_id':round_id,'agent':message['sender_agent']})
                # One retained equivocation per member bounds disk/audit growth.
                if not conn.execute('SELECT 1 FROM collaboration_conflicts WHERE id=?',(conflict_id,)).fetchone():
                    conn.execute('INSERT INTO collaboration_conflicts VALUES(?,?,?)',
                                 (conflict_id, round_id, self._encode(conflict)))
                    self.s.audit.append_in(conn, state['task_id'], None, 'ANCHOR_EQUIVOCATION',
                                           'supervisor', {'round_id': round_id, 'conflict_hash': digest(conflict)})
                return {'status': 'EQUIVOCATION_EXCLUDED'}
            elapsed = self.clock() - state['started_ns']
            if (state['boot_id'] != self.boot_id or elapsed < 0
                    or state['status'] not in {'COLLECTING', 'VIABLE'} or elapsed > state['duration_ns']):
                raise Rejected('ANCHOR_COLLECTION_CLOSED')
            seq = conn.execute('SELECT COUNT(*) FROM collaboration_outputs WHERE round_id=?',
                               (round_id,)).fetchone()[0] + 1
            evidence = {'message': message, 'payload': payload, 'accepted_ns': elapsed, 'receipt_seq': seq}
            self.s.messages.replay.consume_in(conn, message['sender_agent'], message['nonce'], message['issued_at'] / 1000)
            evidence['audit_event_id'] = self.s.audit.append_in(conn, state['task_id'], None,
                                                               'ANCHOR_OUTPUT_ACCEPTED', 'supervisor', evidence)
            conn.execute('INSERT INTO collaboration_outputs VALUES(?,?,?,?)',
                         (round_id, message['sender_agent'], seq, self._encode(evidence)))
            if seq >= state['k']:
                state['status'] = 'VIABLE'
                conn.execute('UPDATE collaboration_rounds SET body=? WHERE id=?', (self._encode(state), round_id))
            return {'status': 'ACCEPTED', 'receipt_seq': seq, 'elapsed_ns': elapsed}

    def close(self, round_id, owner):
        with self.s.lock, self.s.db.transaction() as conn:
            state = self._round(conn, round_id, owner)
            prior = conn.execute('SELECT body FROM collaboration_closed WHERE round_id=?', (round_id,)).fetchone()
            if prior:
                return self._decode(prior)
            if state['boot_id'] == self.boot_id and self.clock() - state['started_ns'] < state['duration_ns']:
                raise Rejected('ANCHOR_FULL_WINDOW_NOT_ELAPSED')
            evidence = [self._decode(r) for r in conn.execute(
                'SELECT body FROM collaboration_outputs WHERE round_id=? ORDER BY receipt_seq', (round_id,))]
            evidence.sort(key=lambda e: (e['payload']['did'], e['message']['payload_hash']))
            # Existing RFC6962 tree is retained under an explicit adaptation ID.
            hashes = [digest(e) for e in evidence]
            completed = len(evidence) >= state['k'] and state['boot_id'] == self.boot_id
            record = {'version': VERSION, 'round_id': round_id, 'task_id': state['task_id'],
                      'snapshot_hash': state['snapshot_hash'], 'policy_id': state['policy_id'],
                      'k': state['k'], 'deadline_ns': state['duration_ns'],
                      'task_revision': state['task_revision'],
                      'started_at_ms': state['started_at_ms'], 'closed_at_ms': int(time.time()*1000),
                      'closure_reason': 'DEADLINE' if state['boot_id'] == self.boot_id else 'COORDINATOR_REBOOT',
                      'status': 'COMPLETED' if completed else 'VOID', 'evidence_count': len(evidence),
                      'evidence_root': merkle.root(hashes), 'verdict': aggregate(evidence) if completed else None,
                      'aggregation_version': AGGREGATION, 'tree_version': 'TRUSTA-RFC6962-V1',
                      'financial_authority': False}
            result = {'record': record, 'evidence': evidence, 'snapshot': state['snapshot'],
                      'inclusion_proofs': [merkle.proof(hashes, i) for i in range(len(hashes))]}
            result['audit_event_id'] = self.s.audit.append_in(conn, state['task_id'], None,
                                                             'ANCHOR_ROUND_CLOSED', 'supervisor', record)
            conn.execute('INSERT INTO collaboration_closed VALUES(?,?)', (round_id, self._encode(result)))
            state['status'] = 'CLOSED_' + record['status']
            conn.execute('UPDATE collaboration_rounds SET body=? WHERE id=?', (self._encode(state), round_id))
            conn.execute('INSERT INTO collaboration_outbox VALUES(?,?,?,?)',
                         (round_id, 'PENDING', digest(record), self._encode({'record': record})))
            return result

    def export(self, round_id, owner):
        result = self.close(round_id, owner)
        while self.s.checkpoints.generate_checkpoint() is not None:
            pass
        result['audit_proof'] = self.s.records.query(owner, audit_event_id=result['audit_event_id'])
        return result
