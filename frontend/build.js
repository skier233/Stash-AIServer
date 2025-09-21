#!/usr/bin/env node
// =============================================================================
// Minimal Build Script for AI Overhaul (Reset State)
// =============================================================================
// Compiles ONLY the minimal frontend components (no business logic)

const fs = require('fs');
const { execSync } = require('child_process');

let files = [
  'src/AIButton.tsx',
  'src/AIButtonIntegration.tsx',
  'src/TaskDashboard.tsx',
  'src/pageContext.ts',
  'src/RecommendedScenes.tsx'
  // (Order not semantically critical; sorted for determinism below)
];
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

for (const file of files) {
  try {
  if (verbose) console.log(`📝 Compiling ${file}...`);
    const jsxFlag = file.endsWith('.tsx') ? '--jsx react' : '';
    execSync(`npx tsc ${file} --target es2019 --module commonjs --lib es2019,dom ${jsxFlag} --outDir dist --declaration false --skipLibCheck true --noResolve`, {
      stdio: 'inherit'
    });

    const outputFile = file.replace('src/', 'dist/').replace(/\.tsx?$/, '.js');
    if (fs.existsSync(outputFile)) {
      let content = fs.readFileSync(outputFile, 'utf8');
      content = content.replace(/"use strict";\n?/, '');
      // Strip CommonJS boilerplate & export assignments, then wrap in IIFE
      content = content
        .replace(/Object\.defineProperty\(exports, "__esModule", \{ value: true \}\);?\n?/g, '')
        .replace(/exports\.[A-Za-z0-9_$]+\s*=\s*/g, '')
        .replace(/module\.exports\s*=\s*[^;]+;?\n?/g, '');
      // Remove stray empty lines after stripping
      content = content.replace(/^[\t ]*\n/gm, '');
      content = wrapIIFE(content.trim());
      fs.writeFileSync(outputFile, content.trim() + '\n');
      if (verbose) console.log(`✅ Output -> ${outputFile}`);
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