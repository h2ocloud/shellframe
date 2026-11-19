/** The extracted desktop settings strings have both locale values. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, 'web', 'i18n.js');
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
    ].every(k => Object.prototype.hasOwnProperty.call(table.en, k) && Object.prototype.hasOwnProperty.call(table['zh-TW'], k)));
  }
}

process.exit(failures ? 1 : 0);
