/** The extracted desktop settings strings have both locale values. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, 'web', 'i18n.js');
const indexFile = path.join(__dirname, 'web', 'index.html');
let failures = 0;
function check(name, ok, detail = '') {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : `  ${detail}`}`);
  if (!ok) failures++;
}

check('web/i18n.js exists', fs.existsSync(file));
if (fs.existsSync(file)) {
  const context = { window: {} };
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
  const table = context.window.SF_I18N;
  check('SF_I18N has en and zh-TW tables', !!table && !!table.en && !!table['zh-TW']);
  if (table && table.en && table['zh-TW']) {
    const en = Object.keys(table.en).sort();
    const zh = Object.keys(table['zh-TW']).sort();
    check('every key exists in both locales', JSON.stringify(en) === JSON.stringify(zh), `en=${en.length}, zh-TW=${zh.length}`);
    check('every locale value is non-empty', en.every(k => String(table.en[k] || '').length > 0)
      && zh.every(k => String(table['zh-TW'][k] || '').length > 0));
    check('the settings batch keys are present', [
      'groupsTitle', 'sttTitle', 'skillTitle', 'skillCopy', 'sttSave',
      'refreshStatusTitle', 'usageBriefTitle', 'usageMissingLabel',
      'pairButton', 'pairButtonTitle', 'noPeersHint', 'pairDeviceTitle',
      'generatePairingCodeTitle', 'pairingCodeTitle', 'joinPairingTitle',
      'maintenanceTitle', 'maintenanceDescription', 'maintenanceCheckUpdate',
    ].every(k => Object.prototype.hasOwnProperty.call(table.en, k) && Object.prototype.hasOwnProperty.call(table['zh-TW'], k)));
  }
}

if (fs.existsSync(indexFile)) {
  const index = fs.readFileSync(indexFile, 'utf8');
  check('dynamic skill status has no static i18n attribute',
    !/<[^>]*id="skill-install-status"[^>]*data-i18n=/.test(index));
  check('skill status loading text uses t()', /el\.textContent\s*=\s*t\('skillLoading'\)/.test(index));
  check('settings footer has a localized tooltip',
    /id="btn-settings"[^>]*data-i18n-title="settings"/.test(index));
  check('narrow sidebar collapses settings to a complete icon button',
    /@container sidebar-footer \(max-width: 185px\)[\s\S]*?#btn-settings/.test(index));
}

process.exit(failures ? 1 : 0);
