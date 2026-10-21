"""Guarded paper Eq.41 censored-lognormal withholding specialization.

An explicit deployment declaration is required. Diagnostics cannot prove IID.
Any permanent failure, drift, dependence or numerical failure uses KM instead.
"""
from dataclasses import dataclass,asdict
import math
import numpy as np
from scipy.optimize import minimize,brentq,minimize_scalar
from scipy.special import ndtr,log_ndtr,gammaln
from scipy.stats import ks_2samp,spearmanr
from backend.domain import Rejected,digest


@dataclass(frozen=True)
class ResponsivePopulation:
    configuration_hash: str
    responsive_agents: tuple[str,...]
    quorum: int
    minimum_rounds: int = 30
    minimum_completed: int = 40
    minimum_censored: int = 0
    bootstrap_samples: int = 99
    significance: float = .05
    maximum_rank_correlation: float = .4
    sigma_bounds: tuple[float,float] = (.1,3.)
    mu_bounds: tuple[float,float] = (-20.,20.)
    version: str = 'ANCHOR-HOMOGENEOUS-WITHHOLDING-V1'

    def validate(self):
        if (not self.configuration_hash or not 1<=self.quorum<=len(self.responsive_agents)<=64
                or len(set(self.responsive_agents))!=len(self.responsive_agents)
                or any(type(v) is not int for v in (self.quorum,self.minimum_rounds,self.minimum_completed,
                        self.minimum_censored,self.bootstrap_samples))
                or not 10<=self.minimum_rounds<=1024 or not 20<=self.minimum_completed<=65536
                or not 0<=self.minimum_censored<=65536 or not 19<=self.bootstrap_samples<=199
                or not .01<=self.significance<=.1 or not .1<=self.maximum_rank_correlation<=.5
                or not -30<=self.mu_bounds[0]<self.mu_bounds[1]<=30
                or not .01<=self.sigma_bounds[0]<self.sigma_bounds[1]<=5):
            raise Rejected('ANCHOR_PARAMETRIC_CONFIGURATION_INVALID')
        return self


def _fit(times,events,spec):
    logs=np.log(times)
    def loss(theta):
        mu,sigma=theta[0],math.exp(theta[1]);z=(logs-mu)/sigma
        return -np.sum(np.where(events,-logs-math.log(sigma)-.5*math.log(2*math.pi)-.5*z*z,log_ndtr(-z)))
    initial=[float(np.mean(logs[events])),math.log(max(spec.sigma_bounds[0],float(np.std(logs[events]))))]
    bounds=[spec.mu_bounds,tuple(math.log(v) for v in spec.sigma_bounds)]
    result=minimize(loss,initial,bounds=bounds,method='L-BFGS-B')
    if not result.success or not math.isfinite(result.fun):raise ValueError('MLE_FAILED')
    mu=float(result.x[0]);sigma=math.exp(float(result.x[1]))
    if any(abs(result.x[i]-edge)<1e-6 for i in (0,1) for edge in bounds[i]):raise ValueError('PARAMETER_BOUNDARY')
    return mu,sigma


def _statistic(times,events,mu,sigma):
    survival=1.;distance=0.
    for t in sorted(set(times)):
        risk=np.sum(times>=t);deaths=np.sum((times==t)&events)
        model=1-float(ndtr((math.log(t)-mu)/sigma))
        distance=max(distance,abs(survival-model));survival*=1-deaths/risk
        distance=max(distance,abs(survival-model))
    return distance


def select_parametric(records,spec,policy,configuration_hash):
    spec.validate()
    metadata={'declaration_hash':digest(asdict(spec)),'bootstrap_samples':spec.bootstrap_samples,
              'iid_claim':'PREDECLARED_WORKING_ASSUMPTION_NOT_PROVEN_BY_DIAGNOSTICS'}
    try:
        if configuration_hash!=spec.configuration_hash:raise ValueError('SPECIALIZATION_INAPPLICABLE')
        if len(records)<spec.minimum_rounds:raise ValueError('INSUFFICIENT_ROUNDS')
        samples=[];m=len(spec.responsive_agents);round_values=[]
        for r in records:
            lookup={o.agent:o for o in r.observations}
            if r.k!=spec.quorum or not set(spec.responsive_agents)<=set(lookup):raise ValueError('POPULATION_CHANGED')
            row=[]
            for agent in spec.responsive_agents:
                o=lookup[agent]
                if o.outcome not in {'VALID','CENSORED'} or o.elapsed_ns<=0:raise ValueError('NON_LATENCY_FAILURE')
                samples.append((o.elapsed_ns/1e9,o.outcome=='VALID',r.horizon_ns/1e9))
                row.append(o.elapsed_ns/1e9)
            # Nonresponsive population must actually withhold, not produce valid output.
            if any(o.outcome!='CENSORED' for a,o in lookup.items() if a not in spec.responsive_agents):
                raise ValueError('WITHHOLDING_DECLARATION_VIOLATED')
            round_values.append(row)
        times=np.array([r[0] for r in samples]);events=np.array([r[1] for r in samples]);horizons=np.array([r[2] for r in samples])
        if sum(events)<spec.minimum_completed or sum(~events)<spec.minimum_censored:raise ValueError('INSUFFICIENT_OBSERVATIONS')
        matrix=np.array(round_values);split=len(matrix)//2
        if ks_2samp(matrix[:split].ravel(),matrix[split:].ravel()).pvalue<.01:raise ValueError('DRIFT_DETECTED')
        for i in range(m):
            for j in range(i):
                rho=float(spearmanr(matrix[:,i],matrix[:,j]).statistic)
                if not math.isfinite(rho) or abs(rho)>spec.maximum_rank_correlation:raise ValueError('DEPENDENCE_DETECTED')
        mu,sigma=_fit(times,events,spec);observed=_statistic(times,events,mu,sigma)
        seed=int(digest([{'sequence':r.sequence,'quorum':r.quorum()} for r in records])[:16],16)
        rng=np.random.default_rng(seed);larger=0
        for _ in range(spec.bootstrap_samples):
            generated=rng.lognormal(mu,sigma,len(times));simulated=np.minimum(generated,horizons);event=generated<=horizons
            if sum(event)<2:raise ValueError('BOOTSTRAP_UNIDENTIFIABLE')
            bmu,bsigma=_fit(simulated,event,spec)
            larger+=_statistic(simulated,event,bmu,bsigma)>=observed
        pvalue=(larger+1)/(spec.bootstrap_samples+1)
        if pvalue<spec.significance:raise ValueError('GOODNESS_OF_FIT_REJECTED')
        k=spec.quorum
        def survival(t):
            f=float(ndtr((math.log(t)-mu)/sigma))
            return sum(math.comb(m,j)*f**j*(1-f)**(m-j) for j in range(k))
        def density(t):
            z=(math.log(t)-mu)/sigma;f=float(ndtr(z))
            if not 0<f<1:return 0.
            logpdf=(gammaln(m+1)-gammaln(k)-gammaln(m-k+1)+(k-1)*math.log(f)
                    +(m-k)*math.log1p(-f)-math.log(t*sigma)-.5*math.log(2*math.pi)-.5*z*z)
            return math.exp(logpdf)
        lower,upper=policy.minimum_ns/1e9,policy.maximum_ns/1e9
        target=policy.waiting_cost_per_second/policy.void_cost
        # Eq.36 is strictly unimodal in log-time. Bracket both sides of its
        # maximum; a coarse sampled grid could miss a narrow pair of roots.
        peak=minimize_scalar(lambda u:-density(math.exp(u)),bounds=(math.log(lower),math.log(upper)),method='bounded')
        if not peak.success or not math.isfinite(peak.fun):raise ValueError('STATIONARY_SEARCH_FAILED')
        grid=[lower,math.exp(float(peak.x)),upper]
        candidates={policy.minimum_ns,policy.maximum_ns}
        for left,right in zip(grid[:-1],grid[1:]):
            if (density(left)-target)*(density(right)-target)<0:
                root=brentq(lambda t:density(t)-target,left,right)
                candidates.update({max(policy.minimum_ns,min(policy.maximum_ns,int(root*1e9)+d)) for d in (0,1)})
        values=[(policy.waiting_cost_per_second*t/1e9+policy.void_cost*survival(t/1e9),t) for t in candidates]
        if not all(math.isfinite(v) for v,_ in values):raise ValueError('NONFINITE_COST')
        cost,deadline=min(values)
        return {'accepted':True,**metadata,'model':'CENSORED_LOGNORMAL_WITHHOLDING','deadline_ns':deadline,
                'estimated_cost':cost,'estimated_void_probability':survival(deadline/1e9),
                'mu':mu,'sigma':sigma,'bootstrap_pvalue':pvalue,'stationary_candidates_ns':sorted(candidates)}
    except (ValueError,FloatingPointError,OverflowError) as exc:
        return {'accepted':False,**metadata,'reason':str(exc)}
