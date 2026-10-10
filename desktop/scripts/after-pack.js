// electron-builder afterPack hook: refresh the bundled build info, then ad-hoc sign the whole bundle.
// Without a Developer ID the app would otherwise carry broken linker signatures, which Gatekeeper reports
// as a damaged app.
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// Resources/build-info.json comes from build/build-info.json (scripts/build-server.sh). A release can reuse
// a cached server build, so rewrite it here with this package's commit, version and time; the app passes
// these to the server as FORGE_COMMIT and FORGE_BUILT_AT.
function writeBuildInfo(context, app) {
  const file = path.join(app, 'Contents', 'Resources', 'build-info.json');
  let commit = null;
  try {
    commit = execFileSync('git', ['rev-parse', '--short', 'HEAD'], { cwd: context.packager.projectDir, encoding: 'utf8' }).trim() || null;
  } catch {
    // not a git checkout: keep whatever build-server.sh recorded
  }
  let previous = {};
  try {
    previous = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    // missing or unreadable: start fresh
  }
  const version = context.packager.appInfo.version;
  const info = {
    version,
    commit: commit || previous.commit || null,
    built_at: new Date().toISOString().replace(/\.\d{3}Z$/, 'Z'),
    channel: version.includes('-preview') ? 'preview' : 'release',
  };
  fs.writeFileSync(file, JSON.stringify(info, null, 2) + '\n');
  console.log(`  • build info ${JSON.stringify(info)}`);
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  writeBuildInfo(context, app);
  execFileSync('xattr', ['-cr', app]); // Finder info and similar attributes break codesign
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' });
  execFileSync('codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' });
};
