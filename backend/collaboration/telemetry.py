"""Post-deadline observation never changes the formal closed evidence set."""
from dataclasses import asdict
from backend.domain import Rejected,digest
from backend.collaboration.otc import Observation,RoundTelemetry
from backend.collaboration.rounds import PAYLOAD_FIELDS,VERSION


def cohort_hash(members,k,policy_id):
    return digest({'members':{a:{n:members[a][n] for n in ('model_commitment','capability_hash')}
                              for a in sorted(members)},'k':k,'policy_id':policy_id})


class CollaborationTelemetry:
    def __init__(self,rounds):
        self.rounds,self.s=rounds,rounds.s
        with self.s.db.transaction() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS collaboration_late(round_id TEXT,agent TEXT,body TEXT,PRIMARY KEY(round_id,agent))')
            conn.execute('CREATE TABLE IF NOT EXISTS collaboration_telemetry(sequence INTEGER PRIMARY KEY AUTOINCREMENT,round_id TEXT UNIQUE,body TEXT)')

    def observe_late(self,round_id,owner,bundle):
        message,payload=bundle['message'],bundle['payload']
        with self.s.db.transaction() as conn:state=self.rounds._round(conn,round_id,owner)
        member=state['snapshot']['members'].get(message.get('sender_agent'))
        if not member:raise Rejected('ANCHOR_NOT_ADMITTED')
        self.rounds.admission.check_current(message['sender_agent'],member,state['policy_id'])
        with self.s.lock,self.s.db.transaction() as conn:
            state=self.rounds._round(conn,round_id,owner)
            elapsed=self.rounds.clock()-state['started_ns']
            if (state['boot_id']!=self.rounds.boot_id or not state['duration_ns']<elapsed<=state.get('telemetry_horizon_ns',state['duration_ns'])
                    or set(payload)!=PAYLOAD_FIELDS or payload['version']!=VERSION
                    or payload['round_id']!=round_id or payload['snapshot_hash']!=state['snapshot_hash']
                    or payload['did']!=member['did'] or payload['epoch']!=member['epoch']
                    or payload['policy_id']!=state['policy_id'] or payload['model_commitment']!=member['model_commitment']
                    or message['task_id']!=state['task_id'] or message['step_id']!='anchor-round:'+round_id
                    or message['payload_ref']!='anchor:'+round_id+':'+message['sender_agent']
                    or message['payload_version']!=state['task_revision'] or message['message_type']!='READ_ONLY_REPORT'
                    or message['sender_key_id']!=member['key_id'] or message['capability_snapshot_hash']!=member['capability_hash']):
                raise Rejected('ANCHOR_LATE_TELEMETRY_CONTEXT_INVALID')
            self.rounds._assessment(payload['assessment']);self.s.messages.verify_in(message,payload,'supervisor',conn)
            cap=payload['assessment']['capability'];agent=message['sender_agent']
            if (cap=='finance.summary' and agent!='finance' or cap=='wealth.recommend' and agent!='wealth'):
                raise Rejected('ANCHOR_ASSESSMENT_CAPABILITY_DENIED')
            if cap=='account.get_balance':self.s.registry.check_bank(agent,'balance.read')
            self.rounds.admission.check_current_in(conn,message['sender_agent'],member,state['policy_id'])
            if conn.execute('SELECT 1 FROM collaboration_outputs WHERE round_id=? AND agent=?',(round_id,message['sender_agent'])).fetchone():
                raise Rejected('ANCHOR_ALREADY_OBSERVED')
            observation=Observation(message['sender_agent'],elapsed,'VALID')
            conn.execute('INSERT OR IGNORE INTO collaboration_late VALUES(?,?,?)',
                         (round_id,message['sender_agent'],self.rounds._encode(asdict(observation))))
        return {'formal_evidence_included':False,'elapsed_ns':elapsed}

    def capture(self,round_id,owner):
        with self.s.lock,self.s.db.transaction() as conn:
            state=self.rounds._round(conn,round_id,owner)
            if (state['boot_id']!=self.rounds.boot_id
                    or self.rounds.clock()-state['started_ns']<state.get('telemetry_horizon_ns',state['duration_ns'])):
                raise Rejected('ANCHOR_TELEMETRY_HORIZON_NOT_OBSERVED')
            prior=conn.execute('SELECT sequence,body FROM collaboration_telemetry WHERE round_id=?',(round_id,)).fetchone()
            if prior:return self._record(prior)
            if not conn.execute('SELECT 1 FROM collaboration_closed WHERE round_id=?',(round_id,)).fetchone():
                raise Rejected('ANCHOR_NOT_CLOSED')
            observations={}
            for row in conn.execute('SELECT body FROM collaboration_outputs WHERE round_id=?',(round_id,)):
                e=self.rounds._decode(row);agent=e['message']['sender_agent']
                observations[agent]=Observation(agent,e['accepted_ns'],'VALID')
            for row in conn.execute('SELECT body FROM collaboration_late WHERE round_id=?',(round_id,)):
                o=Observation(**self.rounds._decode(row));observations.setdefault(o.agent,o)
            for agent in state['snapshot']['members']:
                observations.setdefault(agent,Observation(agent,state.get('telemetry_horizon_ns',state['duration_ns']),'CENSORED'))
            configuration_hash=cohort_hash(state['snapshot']['members'],state['k'],state['policy_id'])
            sequence=conn.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM collaboration_telemetry').fetchone()[0]
            body={'sequence':sequence,'k':state['k'],'horizon_ns':state.get('telemetry_horizon_ns',state['duration_ns']),
                  'observations':[asdict(observations[a]) for a in sorted(observations)],'configuration_hash':configuration_hash}
            conn.execute('INSERT INTO collaboration_telemetry(sequence,round_id,body) VALUES(?,?,?)',(sequence,round_id,self.rounds._encode(body)))
            row=conn.execute('SELECT sequence,body FROM collaboration_telemetry WHERE round_id=?',(round_id,)).fetchone()
            return self._record(row)

    def _record(self,row):
        body=self.rounds._decode(row)
        if row['sequence']!=body['sequence']:raise Rejected('ANCHOR_TELEMETRY_SEQUENCE_TAMPERED')
        return RoundTelemetry(row['sequence'],body['k'],body['horizon_ns'],
                              tuple(Observation(**o) for o in body['observations']),body['configuration_hash']).validate()

    def history(self):
        return [self._record(row) for row in reversed(self.s.db.rows('SELECT sequence,body FROM collaboration_telemetry ORDER BY sequence DESC LIMIT 1024'))]
