"""Real independent M1 restricted-statistics proof; no bank execution authority."""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
from backend.domain import Rejected, canonical, digest


STATEMENT = 'TRUSTA-ANCHOR-M1-STATISTICS-V1'
STATEMENT_FIELDS = frozenset({'version', 'protocol_domain', 'chain_id', 'did', 'message_key_hash',
                             'model_commitment', 'benchmark_root', 'evaluator_version', 'threshold',
                             'policy_id', 'epoch', 'score_encoding'})
HEX = re.compile('[0-9a-f]{64}\\Z')
FILES = frozenset({'anchor_capability_v1.zkey', 'verification_key.json',
                   'anchor_capability_v1_js/anchor_capability_v1.wasm',
                   'anchor_capability_v1.r1cs', 'AnchorCapabilityVerifier.sol'})


def validate_statement(statement):
    if (not isinstance(statement, dict) or set(statement) != STATEMENT_FIELDS
            or statement['version'] != STATEMENT or statement['score_encoding'] != 'anchor-f1-micro-v1'
            or any(type(statement[n]) is not int for n in ('chain_id', 'threshold', 'epoch'))
            or not 0 < statement['chain_id'] < 2**32 or not 0 <= statement['threshold'] <= 1_000_000
            or not 0 <= statement['epoch'] < 2**63
            or any(not isinstance(statement[n], str) or not HEX.fullmatch(statement[n])
                   for n in ('message_key_hash', 'model_commitment', 'benchmark_root', 'evaluator_version', 'policy_id'))
            or not isinstance(statement['protocol_domain'], str) or not 1 <= len(statement['protocol_domain']) <= 192
            or not isinstance(statement['did'], str) or not re.fullmatch(r'did:(weid|key):[A-Za-z0-9:.]+', statement['did'])):
        raise Rejected('ANCHOR_M1_STATEMENT_INVALID')
    return statement


def statement_hash(statement):
    validate_statement(statement)
    return hashlib.sha256(b'TRUSTA/ANCHOR/M1/STATEMENT/V1\0' + canonical(statement).encode()).hexdigest()


def bits(raw):
    return [(b >> shift) & 1 for b in raw for shift in range(7, -1, -1)]


def limbs(raw):
    return [int.from_bytes(raw[i:i+8], 'big') for i in range(0, 32, 8)]


def build_witness(statement, *, tp, fp, fn, prediction_hash, blinding=None):
    validate_statement(statement)
    if (any(type(v) is not int or not 0 <= v < 2**32 for v in (tp, fp, fn))
            or not isinstance(prediction_hash, str) or not HEX.fullmatch(prediction_hash)):
        raise Rejected('ANCHOR_M1_EVALUATION_INVALID')
    denominator = 2*tp+fp+fn
    if denominator == 0:
        raise Rejected('ANCHOR_M1_EMPTY_EVALUATION')
    score = 2_000_000 * tp // denominator
    if score < statement['threshold']:
        raise Rejected('ANCHOR_M1_BELOW_THRESHOLD')
    if blinding is None:
        blinding = secrets.token_bytes(16)
    if not isinstance(blinding, bytes) or len(blinding) != 16:
        raise Rejected('ANCHOR_M1_BLINDING_INVALID')
    hashes = [statement_hash(statement), statement['model_commitment'], statement['benchmark_root'],
              statement['evaluator_version'], statement['policy_id'],
              hashlib.sha256(statement['score_encoding'].encode()).hexdigest()]
    context_bytes = [bytes.fromhex(v) for v in hashes]
    commitment = hashlib.sha256(score.to_bytes(4, 'big') + blinding).digest()
    transcript = hashlib.sha256(b'TRUSTAM1STATv1' + b''.join(context_bytes) + bytes.fromhex(prediction_hash)
                                + b''.join(v.to_bytes(4, 'big') for v in (tp, fp, fn, score))).digest()
    witness = {'context': [limbs(v) for v in context_bytes], 'context_bits': [bits(v) for v in context_bytes],
               'scoreCommitment': limbs(commitment), 'evaluationTranscript': limbs(transcript),
               'threshold': statement['threshold'], 'tp': tp, 'fp': fp, 'fn': fn, 'score': score,
               'prediction_bits': bits(bytes.fromhex(prediction_hash)), 'blinding_bits': bits(blinding)}
    expected = [str(v) for group in witness['context'] for v in group]
    expected += [str(v) for v in witness['scoreCommitment'] + witness['evaluationTranscript']]
    expected.append(str(statement['threshold']))
    return witness, expected


def context_signals(statement):
    """Verifier-derived first 24 inputs, never accepted from an Agent's proof."""
    validate_statement(statement)
    hashes = [statement_hash(statement), statement['model_commitment'], statement['benchmark_root'],
              statement['evaluator_version'], statement['policy_id'],
              hashlib.sha256(statement['score_encoding'].encode()).hexdigest()]
    return [str(v) for h in hashes for v in limbs(bytes.fromhex(h))]


class CapabilityProofEngine:
    def __init__(self, directory, manifest_sha256, *, verification_only=False):
        if type(verification_only) is not bool:raise Rejected('ANCHOR_M1_MODE_INVALID')
        self.verification_only=verification_only
        self.root = Path(directory).resolve()
        self.pin = manifest_sha256
        self.node = shutil.which('node')
        self.runner = Path(__file__).resolve().parents[2] / 'crypto_trust/anchor_proof_runner.mjs'
        self.compute = threading.BoundedSemaphore(1)
        self._assets()
        if not self.node:
            raise Rejected('ANCHOR_M1_NODE_UNAVAILABLE')

    def _assets(self):
        try:
            raw = (self.root/'manifest.json').read_bytes()
            if not HEX.fullmatch(self.pin) or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), self.pin):
                raise ValueError()
            manifest = json.loads(raw)
            if (manifest['statement'] != STATEMENT or manifest['scheme'] != 'Groth16'
                    or manifest['curve'] != 'bn128' or manifest['public_input_count'] != 33
                    or manifest['production_ready'] is not False or set(manifest['files']) != FILES):
                raise ValueError()
            for name, expected in manifest['files'].items():
                if self.verification_only and name!='verification_key.json':continue
                path = (self.root/name).resolve()
                if not path.is_relative_to(self.root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError()
            self.manifest = manifest
        except (ValueError, TypeError, KeyError, OSError):
            raise Rejected('ANCHOR_M1_ARTIFACT_PIN_FAILED') from None

    def _run(self, operation, **values):
        self._assets()
        if operation == 'prove':
            # JSON numbers cannot carry uint64 limbs through JavaScript exactly.
            # Circom accepts decimal strings for every scalar witness element.
            def exact(value):
                if type(value) is int:
                    return str(value)
                if isinstance(value, list):
                    return [exact(v) for v in value]
                if isinstance(value, dict):
                    return {k: exact(v) for k, v in value.items()}
                return value
            values['witness'] = exact(values['witness'])
        if not self.compute.acquire(blocking=False):
            raise Rejected('ANCHOR_M1_BUSY')
        try:
            result = subprocess.run([self.node, str(self.runner)],
                                    input=canonical({'operation': operation, 'root': str(self.root), **values}),
                                    capture_output=True, text=True, encoding='utf-8', timeout=180,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or len(result.stdout) > 16384:
                raise Rejected('ANCHOR_M1_PROOF_REJECTED')
            return json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise Rejected('ANCHOR_M1_PROOF_REJECTED') from None
        finally:
            self.compute.release()

    def prove(self, statement, evaluation):
        """For the trusted evaluator only; admission MUST authenticate its attestation."""
        if self.verification_only:raise Rejected('ANCHOR_M1_VERIFIER_HAS_NO_PROVER_AUTHORITY')
        witness, expected = build_witness(statement, **evaluation)
        result = self._run('prove', witness=witness)
        if result['publicSignals'] != expected:
            raise Rejected('ANCHOR_M1_PUBLIC_INPUT_MISMATCH')
        return {'statement': statement, 'proof': result['proof'], 'public_signals': expected,
                'manifest_sha256': self.pin, 'production_ready': False}

    def verify(self, bundle, expected_statement, expected_public_signals):
        try:
            if len(canonical(bundle).encode('utf-8'))>16384:
                raise ValueError()
            validate_statement(expected_statement)
            if (set(bundle) != {'statement', 'proof', 'public_signals', 'manifest_sha256', 'production_ready'}
                    or bundle['statement'] != expected_statement or bundle['manifest_sha256'] != self.pin
                    or bundle['production_ready'] is not False
                    or not isinstance(expected_public_signals, list) or len(expected_public_signals) != 33
                    or any(not isinstance(v, str) or not re.fullmatch('0|[1-9][0-9]{0,76}', v) for v in expected_public_signals)
                    or expected_public_signals[:24] != context_signals(expected_statement)
                    or expected_public_signals[-1] != str(expected_statement['threshold'])
                    or bundle['public_signals'] != expected_public_signals):
                raise ValueError()
            if self._run('verify', proof=bundle['proof'], publicSignals=expected_public_signals).get('verified') is not True:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise Rejected('ANCHOR_M1_PROOF_REJECTED') from None
        return {'verified': True, 'statement_hash': statement_hash(expected_statement),
                'proof_hash': digest(bundle), 'financial_authority': False, 'production_ready': False}
