#!/usr/bin/env node
// Minimal build for AI Overhaul core components (no visual change intent)
// Compiles a fixed list of standalone TS/TSX files individually and wraps each output in an IIFE.

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

let files = [
  'src/pageContext.ts',
  'src/InteractionTracker.ts',
  'src/RecommendationUtils.tsx',
  'src/RecommendedScenes.tsx',
  'src/SimilarScenes.tsx',
  'src/SimilarTabIntegration.tsx',
  'src/AIButton.tsx',
  'src/AIButtonIntegration.tsx',
  'src/TaskDashboard.tsx',
  'src/PluginSettings.tsx',
  'src/BackendBase.ts',
  'src/BackendHealth.ts',
  'src/plugin_setup.py',
  'src/css/recommendedscenes.css',
  'src/css/SimilarScenes.css',
  'src/css/AIOverhaul.css',
  'src/AIOverhaul.yml'
].sort();
const verbose = !!process.env.BUILD_VERBOSE;

function wrapIIFE(code) { return `(function(){\n${code}\n})();\n`; }

if (fs.existsSync('dist')) fs.rmSync('dist', { recursive: true, force: true });
fs.mkdirSync('dist');

// Copy any non-TS files from `src` into `dist`, preserving directory structure.
function copyNonTsFiles(srcDir, destDir) {
  if (!fs.existsSync(srcDir)) return [];
  const copied = [];
  const walk = (cur) => {
    for (const name of fs.readdirSync(cur)) {
      const full = path.join(cur, name);
      const stat = fs.statSync(full);
      if (stat.isDirectory()) {
        walk(full);
        continue;
      }
      const rel = path.relative(srcDir, full);
      if (rel.match(/\.tsx?$/i)) continue; // skip ts/tsx source files
      const target = path.join(destDir, rel);
      const targetDir = path.dirname(target);
      fs.mkdirSync(targetDir, { recursive: true });
      fs.copyFileSync(full, target);
      copied.push({ src: full, dest: target });
    }
  };
  walk(srcDir);
  return copied;
}

console.log('🔨 Building minimal AI Overhaul...');
if (verbose) console.log('Files:', files.join(', '));

let failed = 0; let fileIndex = 0;

// Detect whether the TypeScript compiler is available via npx. If it's not present
// we will skip compilation steps and still copy non-TS assets so the build can
// be used in environments without developer dependencies installed.
let compileAvailable = true;
try {
  execSync('npx tsc --version', { stdio: 'ignore' });
} catch (e) {
  compileAvailable = false;
  if (verbose) console.warn('tsc not available via npx; skipping TypeScript compilation (assets will still be copied)');
}
for (const file of files) {
  try {
    if (verbose) console.log('→', file);
    // If it's a TS/TSX file, invoke tsc. Otherwise, copy the file into dist preserving
    // the relative path from src so non-TS assets are available.
    if (/\.tsx?$/.test(file)) {
      if (!compileAvailable) {
        if (verbose) console.log('skipping compile of', file);
      } else {
        const jsxFlag = file.endsWith('.tsx') ? '--jsx react' : '';
        execSync(`npx tsc ${file} --target es2019 --module commonjs --lib es2019,dom ${jsxFlag} --esModuleInterop --outDir dist --declaration false --skipLibCheck true`, { stdio: 'inherit' });
      }
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
    } else {
      // copy non-TS file directly into dist preserving src relative path
      try {
        if (!fs.existsSync(file)) {
          if (verbose) console.warn('asset not found, skipping:', file);
        } else {
          const rel = path.relative('src', file);
          const target = path.join('dist', rel);
          fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.copyFileSync(file, target);
        }
      } catch (copyErr) {
        failed++;
        console.error('❌ Failed to copy asset:', file, copyErr && copyErr.message ? copyErr.message : copyErr);
      }
    }
  } catch (err) { failed++; console.error('❌ Failed:', file, err.message); }
}

if (failed) console.error(`⚠ Build finished with ${failed} failure(s)`); else console.log('🎉 Minimal build complete');
try {
  const distFiles = fs.readdirSync('dist').filter(f=>f.endsWith('.js'));
  let total = 0; for (const f of distFiles) { const s = fs.statSync('dist/'+f); total += s.size; console.log(` • dist/${f} (${Math.round(s.size)} bytes)`); }
  console.log(` Σ Total size: ${Math.round(total)} bytes`);
} catch {}

// Now copy non-TS assets from src into dist so things like CSS/Python/etc are available
try {
  const copied = copyNonTsFiles(path.join(process.cwd(), 'src'), path.join(process.cwd(), 'dist'));
  if (copied.length) {
    console.log('📦 Copied non-TS assets:');
    for (const item of copied) console.log(' •', path.relative(process.cwd(), item.dest));
  }
} catch (err) { if (verbose) console.error('Error copying non-TS assets', err); }
process.exitCode = failed ? 1 : 0;