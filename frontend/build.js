#!/usr/bin/env node
// =============================================================================
// Minimal Build Script for AI Overhaul (Reset State)
// =============================================================================
// Compiles ONLY the minimal frontend components (no business logic)

const fs = require('fs');
const { execSync } = require('child_process');
const path = require('path');

let files = [
  'src/pageContext.ts',
  'src/RecommendedScenes.tsx',
  'src/AIButton.tsx',
  'src/AIButtonIntegration.tsx',
  'src/TaskDashboard.tsx'
]; // Legacy recommendation harness removed
files = files.sort();
const verbose = !!process.env.BUILD_VERBOSE;

// Simple wrapper template to isolate scope & avoid stray exports in browser context
function wrapIIFE(code) {
  return `(function(){\n${code}\n})();\n`;
}

// Clean dist directory
if (fs.existsSync('dist')) {
  fs.rmSync('dist', { recursive: true, force: true });
}
fs.mkdirSync('dist');

console.log('🔨 Building minimal AI Overhaul...\n(files: ' + files.length + ')');
if (verbose) console.log('File order:', files.join(', '));

let failed = 0;
let fileIndex = 0;

for (const file of files) {
  try {
  if (verbose) console.log(`📝 Compiling ${file}...`);
    const jsxFlag = file.endsWith('.tsx') ? '--jsx react' : '';
    // Compile each file individually. We ENABLE module resolution now (removed --noResolve)
    // and add --esModuleInterop so default React imports work without rewriting source.
    execSync(`npx tsc ${file} --target es2019 --module commonjs --lib es2019,dom ${jsxFlag} --esModuleInterop --outDir dist --declaration false --skipLibCheck true`, {
      stdio: 'inherit'
    });

    // TypeScript (single-file compile) flattens output to dist/<basename>.js regardless of subfolders
    const flatOutput = path.join('dist', path.basename(file).replace(/\.tsx?$/, '.js'));
    if (fs.existsSync(flatOutput)) {
      let content = fs.readFileSync(flatOutput, 'utf8');
      // Normalize Windows line endings first for consistent regex stripping
      content = content.replace(/\r\n/g, '\n');
      content = content.replace(/"use strict";\n?/, '');
      // Strip CommonJS boilerplate & export assignments, then wrap in IIFE
      // Expose certain modules globally when they declare marker comments (light heuristic)
      // (Removed legacy harness global export heuristics)
      content = content
        .replace(/Object\.defineProperty\(exports, "__esModule", \{ value: true \}\);?\n?/g, '')
        .replace(/exports\.[A-Za-z0-9_$]+\s*=\s*/g, '')
        .replace(/module\.exports\s*=\s*[^;]+;?\n?/g, '');
      // Remove stray empty lines after stripping
      content = content.replace(/^[\t ]*\n/gm, '');
      // Uniquify auto-generated CJS helper require variables (e.g., types_1, api_1) in case
      // the host concatenates plugin scripts into a single scope before execution.
      const requireVars = Array.from(content.matchAll(/(?:var|let|const)\s+([A-Za-z0-9_$]+_1)\s*=\s*require\(/g)).map(m => m[1]);
      const seen = new Set();
      for (const rv of requireVars) {
        if (seen.has(rv)) continue;
        seen.add(rv);
        const unique = rv + '_' + fileIndex;
        const re = new RegExp('\\b' + rv + '\\b', 'g');
        content = content.replace(re, unique);
      }
      fileIndex++;
  content = wrapIIFE(content.trim());
      fs.writeFileSync(flatOutput, content.trim() + '\n');
      if (verbose) console.log(`✅ Output -> ${flatOutput}`);
    }
  } catch (err) {
    failed++;
    console.error(`❌ Failed to compile ${file}:`, err.message);
  }
}

if (failed) {
  console.error(`\n⚠ Build finished with ${failed} failure(s)`);
} else {
  console.log('\n🎉 Minimal build complete');
}
try {
  const distFiles = fs.readdirSync('dist').filter(f => f.endsWith('.js'));
  let total = 0;
  distFiles.forEach(f => { const s = fs.statSync(`dist/${f}`); total += s.size; console.log(`   • dist/${f} (${Math.round(s.size)} bytes)`); });
  console.log(`   Σ Total size: ${Math.round(total)} bytes`);
} catch (_) {
  console.log('   (no dist files)');
}
process.exitCode = failed ? 1 : 0;