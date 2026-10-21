"""Between-round Full-window deadline selection (ANCHOR §3.6).

Times are coordinator-observed integer nanoseconds. The empirical estimator
retains administrative censoring and never sees the current round's trajectory.
The homogeneous model is deliberately unavailable unless its deployment and
statistical gates have been independently satisfied; no IID assumption is made
from a successful marginal fit alone.
"""
from dataclasses import asdict, dataclass
import math
from backend.domain import Rejected, digest


OUTCOMES = frozenset({'VALID', 'CENSORED', 'INVALID', 'ERROR', 'CANCELLED'})


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value < 2**63:
        raise Rejected('ANCHOR_INVALID_TIME_OR_COUNT')
    return value


@dataclass(frozen=True)
class Observation:
    agent: str
    elapsed_ns: int
    outcome: str


@dataclass(frozen=True)
class RoundTelemetry:
    sequence: int
    k: int
    horizon_ns: int
    observations: tuple[Observation, ...]
    configuration_hash: str

    def validate(self):
        integer(self.sequence)
        integer(self.k, 1)
        integer(self.horizon_ns, 1)
        if (not self.observations or self.k > len(self.observations)
                or len(self.observations) > 64
                or len({o.agent for o in self.observations}) != len(self.observations)):
            raise Rejected('ANCHOR_INVALID_TELEMETRY')
        for o in self.observations:
            integer(o.elapsed_ns)
            if (not isinstance(o.agent, str) or not o.agent
                    or o.outcome not in OUTCOMES or o.elapsed_ns > self.horizon_ns
                    or (o.outcome == 'CENSORED' and o.elapsed_ns != self.horizon_ns)):
                raise Rejected('ANCHOR_INVALID_TELEMETRY')
        return self

    def quorum(self):
        self.validate()
        completed = sorted(o.elapsed_ns for o in self.observations if o.outcome == 'VALID')
        return (completed[self.k-1], True) if len(completed) >= self.k else (self.horizon_ns, False)


def km_survival(samples, deadline):
    """Product-limit survival; tied events precede tied censor withdrawals."""
    if not samples:
        raise Rejected('ANCHOR_NO_TELEMETRY')
    survival = 1.0
    for t in sorted({t for t, _ in samples if t <= deadline}):
        at_risk = sum(t0 >= t for t0, _ in samples)
        events = sum(t0 == t and observed for t0, observed in samples)
        survival *= 1 - events / at_risk
    return survival


@dataclass(frozen=True)
class DeadlinePolicy:
    minimum_ns: int
    maximum_ns: int
    safe_default_ns: int
    waiting_cost_per_second: int
    void_cost: int
    candidates_ns: tuple[int, ...]
    history_window: int = 20
    version: str = 'trusta-anchor-otc-km-v1'

    def validate(self):
        for name in ('minimum_ns', 'maximum_ns', 'safe_default_ns',
                     'waiting_cost_per_second', 'void_cost', 'history_window'):
            integer(getattr(self, name), 1)
        if (not self.minimum_ns < self.maximum_ns
                or not self.minimum_ns <= self.safe_default_ns <= self.maximum_ns
                or not isinstance(self.candidates_ns, tuple)
                or len(self.candidates_ns) > 4096
                or len(set(self.candidates_ns)) != len(self.candidates_ns)
                or not {self.minimum_ns, self.maximum_ns} <= set(self.candidates_ns)
                or any(type(t) is not int or not self.minimum_ns <= t <= self.maximum_ns for t in self.candidates_ns)
                or not 1 <= self.history_window <= 1024):
            raise Rejected('ANCHOR_INVALID_DEADLINE_POLICY')
        return self


class AdaptiveOTC:
    def __init__(self, policy, *, responsive_population=None):
        self.policy = policy.validate()
        self.population=responsive_population.validate() if responsive_population is not None else None

    def select(self, history, *, next_sequence, configuration_hash, previous_deadline=None):
        integer(next_sequence)
        records = tuple(history)
        if (any(r.sequence >= next_sequence for r in records)
                or len({r.sequence for r in records}) != len(records)):
            raise Rejected('ANCHOR_FUTURE_TELEMETRY_DENIED')
        for r in records:
            r.validate()
        window = sorted((r for r in records if r.configuration_hash == configuration_hash),
                        key=lambda r: r.sequence)[-self.policy.history_window:]
        # Do not extrapolate an unobserved tail beyond the telemetry horizon.
        usable = [r for r in window if r.horizon_ns >= self.policy.maximum_ns]
        metadata = {'version': self.policy.version, 'policy_hash': digest(asdict(self.policy)),
                    'telemetry_horizon_ns':self.policy.maximum_ns,
                    'history_hash': digest([asdict(r) for r in usable]),
                    'history_sequences': [r.sequence for r in usable],
                    'parametric_gate': 'BYPASS_UNVALIDATED_IID_WITHHOLDING_SPECIALIZATION'}
        if not usable:
            default = self.policy.safe_default_ns if previous_deadline is None else integer(previous_deadline, 1)
            return metadata | {'deadline_ns': min(self.policy.maximum_ns, max(self.policy.minimum_ns, default)),
                               'model': 'SAFE_DEFAULT', 'fallback_reason': 'INSUFFICIENT_OBSERVATION_HORIZON',
                               'estimated_void_probability': None, 'estimated_cost': None}
        if self.population is not None:
            from backend.collaboration.otc_model import select_parametric
            checked=select_parametric(usable,self.population,self.policy,configuration_hash)
            metadata['parametric_gate']=checked
            if checked['accepted']:
                return metadata|{k:v for k,v in checked.items() if k!='accepted'}|{'fallback_reason':None}
        samples = [r.quorum() for r in usable]
        costs = [(self.cost(t, km_survival(samples, t)), t) for t in self.policy.candidates_ns]
        cost, selected = min(costs)
        return metadata | {'deadline_ns': selected, 'model': 'EMPIRICAL_KM',
                           'fallback_reason': metadata['parametric_gate'],
                           'estimated_void_probability': km_survival(samples, selected),
                           'estimated_cost': cost}

    def cost(self, deadline, survival):
        cost = self.policy.waiting_cost_per_second * deadline / 1_000_000_000 + self.policy.void_cost * survival
        if not math.isfinite(cost):
            raise Rejected('ANCHOR_INVALID_COST')
        return cost

    def p95(self, history):
        records = tuple(history)
        for r in records:
            r.validate()
        samples = [r.quorum() for r in records]
        times = sorted({t for t, event in samples if event})
        value = next((t for t in times if km_survival(samples, t) <= .05), self.policy.maximum_ns)
        return min(self.policy.maximum_ns, max(self.policy.minimum_ns, value))

    def oracle_offline(self, complete_history):
        """Evaluation-only API. Never used by select() or the round runtime."""
        records = tuple(complete_history)
        for r in records:
            r.validate()
        if not records or any(r.horizon_ns < self.policy.maximum_ns for r in records):
            raise Rejected('ANCHOR_ORACLE_DATA_INCOMPLETE')
        values = [r.quorum()[0] if r.quorum()[1] else math.inf for r in records]
        cost, deadline = min((self.cost(t, sum(y > t for y in values) / len(values)), t)
                             for t in self.policy.candidates_ns)
        return {'scope': 'OFFLINE_ORACLE_ONLY', 'deadline_ns': deadline, 'cost': cost}
