import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(frontendRoot, 'src');
const forbiddenApiOrigin = /https?:\/\/(?:localhost|127\.0\.0\.1):8000/;

function sourceFiles(root) {
  return readdirSync(root).flatMap((name) => {
    const candidate = path.join(root, name);
    if (statSync(candidate).isDirectory()) return sourceFiles(candidate);
    return /\.[cm]?[jt]sx?$/.test(name) ? [candidate] : [];
  });
}

for (const filename of sourceFiles(sourceRoot)) {
  assert.doesNotMatch(readFileSync(filename, 'utf8'), forbiddenApiOrigin, filename);
}

const enterpriseDetail = readFileSync(
  path.join(sourceRoot, 'pages', 'EnterpriseDetailPage.jsx'),
  'utf8',
);
assert.match(
  enterpriseDetail,
  /`\/api\/reports\/enterprise\/\$\{enterpriseId\}\/pdf`/,
);

console.log('PASS production API origin contract');
