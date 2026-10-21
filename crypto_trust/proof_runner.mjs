import fs from 'node:fs';
import path from 'node:path';
import {buildPoseidon} from 'circomlibjs';
import {groth16} from 'snarkjs';

// Private witness travels through stdin and memory only. No witness/log files.
let input='';for await (const chunk of process.stdin) input+=chunk;
try {
  const req=JSON.parse(input);
  const root=path.resolve(req.root);
  let result;
  if(req.operation==='commit') {
    const poseidon=await buildPoseidon();
    result={commitment:poseidon.F.toObject(poseidon([req.witness.balance,req.witness.risk,req.witness.nonce,req.witness.context])).toString()};
  } else if(req.operation==='prove') {
    result=await groth16.fullProve(req.witness,path.join(root,'banking_eligibility_js','banking_eligibility.wasm'),path.join(root,'banking_eligibility.zkey'));
  } else if(req.operation==='verify') {
    const vk=JSON.parse(fs.readFileSync(path.join(root,'verification_key.json'),'utf8'));
    result={verified:await groth16.verify(vk,req.publicSignals,req.proof)};
  } else throw new Error('operation');
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
} catch {
  // Circom witness errors can contain private values: never forward them.
  process.stderr.write('PRIVACY_PROOF_REJECTED');process.exit(2);
}
