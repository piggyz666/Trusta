import fs from 'node:fs';
import path from 'node:path';
import {groth16} from 'snarkjs';

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
  if (input.length > 100000) process.exit(2);
}
try {
  const req = JSON.parse(input);
  const root = path.resolve(req.root);
  let result;
  if (req.operation === 'prove') {
    result = await groth16.fullProve(req.witness,
      path.join(root, 'anchor_capability_v1_js', 'anchor_capability_v1.wasm'),
      path.join(root, 'anchor_capability_v1.zkey'));
  } else if (req.operation === 'verify') {
    const vk = JSON.parse(fs.readFileSync(path.join(root, 'verification_key.json'), 'utf8'));
    result = {verified: await groth16.verify(vk, req.publicSignals, req.proof)};
  } else throw new Error('unsupported operation');
  process.stdout.write(JSON.stringify(result));
  process.exit(0);
} catch {
  // Witness failures may contain confidential counts; never forward diagnostics.
  process.stderr.write('ANCHOR_CAPABILITY_PROOF_REJECTED');
  process.exit(2);
}
