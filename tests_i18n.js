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
      'remoteDefaultName', 'remoteTabTitle', 'remoteTabCloseTitle',
      'peerReachable', 'peerOffline', 'peerNoAddress', 'peerToggleTitle',
      'peerUnpairTitle', 'peerUnpairButton', 'peerModeMaster', 'peerModeMember',
      'peerModeMasterTitle', 'peerModeMemberTitle', 'peerMessage', 'peerMessageTitle',
      'peerFile', 'peerFileTitle', 'peerAddress', 'peerAddressTitle',
      'peerMaintenance', 'peerMaintenanceTitle', 'peerOfflineHint',
      'peerNoAddressHint', 'peerControlledHint', 'remoteTabsLoading',
      'newRemoteSession', 'newRemoteSessionTitle', 'noRemoteTabs',
      'remoteWorking', 'remoteIdle', 'remoteNoStatus', 'remoteSessionCloseTitle',
      'mobileDevices', 'mobileDevicesTitle', 'mobileOnline', 'mobileConnected',
      'mobileUnpairTitle', 'mobileAddressTitle',
      'createSession', 'customCommandPlaceholder', 'creatingSession',
      'sessionCreateFailed', 'sessionCreateError', 'closeRemoteSessionTitle',
      'closeRemoteSessionDescription', 'unpairConfirmTitle', 'unpairDescription',
      'unpairOtherDescription', 'editPeerAddressTitle', 'peerAddressPlaceholder',
      'save', 'messagesTitle', 'messageInputPlaceholder', 'filesTitle',
      'fileDropHint', 'chooseFile', 'downloadsHint', 'queued', 'queuedOffline',
      'fileSent', 'fileReceived', 'me', 'peer', 'sendFailed', 'dropPathError',
      'pairCodeGenerationFailed', 'remoteControlDenied', 'remoteStreamDisconnected', 'remoteAttachFailed', 'remoteCloseSession',
      'usageLoading', 'resetLabel', 'accountNoUsageData', 'accountUnsupported',
      'usageUnavailable', 'snapshotLabel', 'checkedLabel', 'paceLabel',
      'cliManagedLogin', 'currentSession', 'nextSession', 'nextSessionGlobal',
      'switchCurrent', 'setNext', 'installProvider', 'providerBinaryMissing', 'notInstalled',
      'singleAccount', 'notLoggedIn', 'notLoggedInHint', 'accountCount',
      'backendNotReported', 'frontendKnowsCli', 'backendNoAccount',
      'noLoginAccounts', 'loginNewAccount',
      'refreshLoggedIn', 'aiAccountsTitle', 'accountPanelNote', 'paceToggle',
      'refreshUsage', 'close', 'processing', 'queryingUsage', 'usageUpdated',
      'paceEnabled', 'paceDisabled', 'installCommandFailed', 'installTabOpened',
      'accountUpdated', 'loginTabOpened', 'operationFailed', 'accountDataReadFailed',
      'pasteSend', 'pasteCancel', 'boardResizeTitle', 'linkResizeTitle', 'collapse',
      'presetDefault', 'providerNotInstalled', 'providerRunLine', 'providerNotFound',
      'providerInstallOfficial', 'providerDocs', 'providerCancel', 'providerInstallTab',
      'providerInstallFailed', 'wakeNow', 'nextWake', 'cloudSchedule', 'roundsRun',
      'remoteHistoryNoResponse', 'remoteHistoryUnavailable', 'remoteImageOnly',
      'remoteImageFailed', 'usageQuerying', 'usageQueryFailed', 'insert', 'select',
      'updateChecking', 'updateCheckFailed', 'updateAvailable', 'upToDate',
      'updateNow', 'updateFailed', 'newVersionAvailable', 'updating', 'restartRequired',
      'restartCoreHint', 'restartSessionsHint', 'updatedTo', 'availableClick',
      'restarting', 'failedPrefix', 'relaunchApplyHint', 'scheduledLaunch', 'running', 'updateAvailableShort', 'updatingDots',
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
