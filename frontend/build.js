#!/usr/bin/env node
// Minimal build for AI Overhaul core components (no visual change intent)
// Compiles a fixed list of standalone TS/TSX files individually and wraps each output in an IIFE.

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

let files = [
  'src/pageContext.ts',
  'src/RecommendationUtils.tsx',
  'src/RecommendedScenes.tsx',
  'src/SimilarScenes.tsx',
  'src/SimilarTabIntegration.tsx',
  'src/AIButton.tsx',
  'src/AIButtonIntegration.tsx',
  'src/TaskDashboard.tsx'
].sort();
const verbose = !!process.env.BUILD_VERBOSE;

function wrapIIFE(code) { return `(function(){\n${code}\n})();\n`; }

if (fs.existsSync('dist')) fs.rmSync('dist', { recursive: true, force: true });
fs.mkdirSync('dist');

console.log('🔨 Building minimal AI Overhaul...');
if (verbose) console.log('Files:', files.join(', '));

let failed = 0; let fileIndex = 0;
for (const file of files) {
  try {
    if (verbose) console.log('→', file);
    const jsxFlag = file.endsWith('.tsx') ? '--jsx react' : '';
    execSync(`npx tsc ${file} --target es2019 --module commonjs --lib es2019,dom ${jsxFlag} --esModuleInterop --outDir dist --declaration false --skipLibCheck true`, { stdio: 'inherit' });
    const out = path.join('dist', path.basename(file).replace(/\.tsx?$/, '.js'));
    if (fs.existsSync(out)) {
      let content = fs.readFileSync(out, 'utf8').replace(/\r\n/g,'\n')
        .replace(/"use strict";\n?/, '')
        .replace(/Object\.defineProperty\(exports, "__esModule", { value: true }\);?\n?/g,'')
        .replace(/exports\.[A-Za-z0-9_$]+\s*=\s*/g,'')
        .replace(/module\.exports\s*=\s*[^;]+;?\n?/g,'')
        .replace(/^[\t ]*\n/gm,'');
      // Uniquify auto require helpers (types_1, api_1, etc.)
      const requireVars = Array.from(content.matchAll(/(?:var|let|const)\s+([A-Za-z0-9_$]+_1)\s*=\s*require\(/g)).map(m=>m[1]);
      const seen = new Set();
      for (const rv of requireVars) { if (seen.has(rv)) continue; seen.add(rv); content = content.replace(new RegExp('\\b'+rv+'\\b','g'), rv+'_'+fileIndex); }
      fileIndex++;
      fs.writeFileSync(out, wrapIIFE(content.trim()) + '\n');
    }
  } catch (err) { failed++; console.error('❌ Failed:', file, err.message); }
}

if (failed) console.error(`⚠ Build finished with ${failed} failure(s)`); else console.log('🎉 Minimal build complete');
try {
  const distFiles = fs.readdirSync('dist').filter(f=>f.endsWith('.js'));
  let total = 0; for (const f of distFiles) { const s = fs.statSync('dist/'+f); total += s.size; console.log(` • dist/${f} (${Math.round(s.size)} bytes)`); }
  console.log(` Σ Total size: ${Math.round(total)} bytes`);
} catch {}
process.exitCode = failed ? 1 : 0;