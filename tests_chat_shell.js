/**
 * The chat view's rules (web/chat.js), run in node against the real file.
 *
 *   - a question's options: plain words only when every option is known; otherwise
 *     every option exactly as the CLI wrote it; the number sent is always the
 *     option's own number (Claude Code 2.1.319/2.1.322 and Codex 0.142.1 wording)
 *   - tool calls: consecutive ones become one plain line, nothing dropped
 *   - what it is doing: plain words from the status monitor's verdict
 *   - a turn: compaction folded (the phone's test), Telegram/A2A wrapping as a tag,
 *     Codex's doubled answers and empty reasoning left out, [[SF:RED]] tail folded
 *   - markdown: agent text can never become HTML or a non-http link
 *
 * Run: node tests_chat_shell.js
 */
const path = require('path');
const R = require(path.join(__dirname, 'web', 'chat.js'));

let fails = 0, passes = 0;
function check(name, ok, detail) {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${ok ? '' : '  ' + JSON.stringify(detail)}`);
  if (ok) passes++; else fails++;
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const opts = (...texts) => texts.map((t, i) => ({ n: i + 1, text: t }));
const labels = m => m.options.map(o => o.label);
const nums = m => m.options.map(o => o.n);

console.log('questions: Claude Code');
{
  // Measured on Claude Code 2.1.319 (tests_link_bots.py's screen): Bash, three options.
  const d = { id: 'q1', reason: 'Do you want to proceed?',
              options: opts('Yes', 'Yes, and always allow access to /repo from this project', 'No') };
  const zh = R.mapDecision(d, 'zh-TW');
  check('the Bash prompt in plain Chinese', zh.translated && same(labels(zh),
    ['允許', '允許，而且這個專案以後都能存取 /repo', '不允許']), labels(zh));
  check('…numbers unchanged', same(nums(zh), [1, 2, 3]), nums(zh));
  check('…the question too', zh.questionTranslated && zh.question === '要讓它繼續嗎？', zh.question);
  check('…tones: allow / allow / deny', same(zh.options.map(o => o.tone), ['yes', 'yes', 'no']));
  check('…the original words kept with each option', same(zh.options.map(o => o.raw), d.options.map(o => o.text)));
  const en = R.mapDecision(d, 'en');
  check('…and in English', same(labels(en), ['Allow', 'Allow, and let this project always reach /repo', "Don't allow"]), labels(en));
}
{
  const d = { id: 'q2', reason: 'Do you want to proceed?', options: opts('Yes',
    "Yes, and don't ask again for npm run test:* commands in /work/app", 'No, and tell Claude what to do differently (esc)') };
  const m = R.mapDecision(d, 'zh-TW');
  check("don't-ask-again keeps the command and the folder, verbatim", m.translated && same(labels(m),
    ['允許', '允許，之後在 /work/app 執行 npm run test:* 指令都不再問', '不允許，告訴它要怎麼改']), labels(m));
}
{
  const d = { id: 'q3', reason: 'Do you want to make this edit to relay.py?',
              options: opts('Yes', 'Yes, allow all edits during this session (shift+tab)', 'No, and tell Claude what to do differently (esc)') };
  const m = R.mapDecision(d, 'zh-TW');
  check('the edit prompt: key hints dropped, file kept', m.translated && m.question === '要讓它修改 relay.py 嗎？'
    && same(labels(m), ['允許', '允許，這次工作階段的修改都不再問', '不允許，告訴它要怎麼改']), [m.question, labels(m)]);
  const c = R.mapDecision({ id: 'q4', reason: 'Do you want to create notes.md?', options: opts('Yes', 'No') }, 'en');
  check('creating a file', c.question === 'Let it create notes.md?' && same(labels(c), ['Allow', "Don't allow"]), c);
}
console.log('questions: Codex');
{
  // Codex prints the command right above its options, so that is the `reason` it arrives with.
  const d = { id: 'c1', reason: '$ npm test', options: opts('Yes, proceed (y)',
    "Yes, and don't ask again for this command in this session (a)", 'No, and tell Codex what to do differently (esc)') };
  const m = R.mapDecision(d, 'zh-TW');
  check('the command prompt in plain Chinese', m.translated && same(labels(m),
    ['允許', '允許，這次工作階段這個指令不再問', '不允許，告訴它要怎麼改']), labels(m));
  check('…its command line is not a question: kept as written', !m.questionTranslated && m.question === '$ npm test', m.question);
  const n = R.mapDecision({ id: 'c2', reason: 'Do you want to approve network access to "example.com"?',
    options: opts('Yes, just this once', 'Yes, and allow this host in the future', 'No, and block this host in the future') }, 'zh-TW');
  check('network access', n.translated && n.question === '要讓它連到 example.com 嗎？' && same(labels(n),
    ['只允許這一次', '允許，以後都能連這個網站', '不允許，以後都擋掉這個網站']), [n.question, labels(n)]);
  const p = R.mapDecision({ id: 'c3', reason: 'Would you like to run the following command?',
    options: opts('Yes, proceed', "Yes, and don't ask again for commands that start with `git status`", 'No, continue without running it') }, 'zh-TW');
  check('a prefix rule keeps the prefix', p.translated && same(labels(p),
    ['允許', '允許，之後 git status 開頭的指令都不再問', '不執行，讓它繼續']) && p.question === '要讓它執行這個指令嗎？', labels(p));
}
console.log('questions it does not know: as written, all of them');
{
  const model = R.mapDecision({ id: 'm', reason: 'Select model', options: opts('Default (recommended)', 'Opus', 'Sonnet') }, 'zh-TW');
  check('a /model picker: nothing translated', !model.translated && same(labels(model), ['Default (recommended)', 'Opus', 'Sonnet'])
    && model.question === 'Select model', model);
  const mixed = R.mapDecision({ id: 'x', reason: 'Do you want to proceed?',
    options: opts('Yes', 'Yes, and allow access to /x and git commands', 'No') }, 'zh-TW');
  check('one unknown option: every option as written, "Yes" included', !mixed.translated
    && same(labels(mixed), ['Yes', 'Yes, and allow access to /x and git commands', 'No'])
    && mixed.options.every(o => o.tone === ''), labels(mixed));
  check('…and the question with it', mixed.question === 'Do you want to proceed?' || mixed.question === '要讓它繼續嗎？', mixed.question);
  const trust = R.mapDecision({ id: 't', reason: 'Quick safety check', options: opts('Yes, I trust this folder', 'No, exit') }, 'zh-TW');
  check('the folder trust dialog', !trust.translated && same(labels(trust), ['Yes, I trust this folder', 'No, exit']), labels(trust));
  const red = R.mapDecision({ id: 'r', reason: '要先修哪個？', options: [{ n: 1, text: '先修按鈕' }, { n: 2, text: '先修通知' }] }, 'zh-TW');
  check("a bot's own [[SF:RED]] options: its words", !red.translated && same(labels(red), ['先修按鈕', '先修通知']) && red.question === '要先修哪個？', red);
  const plan = R.mapDecision({ id: 'p', reason: 'Would you like to proceed?',
    options: opts('Yes, and auto-accept edits', 'Yes, manually approve edits', 'No, keep planning') }, 'zh-TW');
  check('the plan dialog (not in the rules): as written', !plan.translated && same(labels(plan),
    ['Yes, and auto-accept edits', 'Yes, manually approve edits', 'No, keep planning']), labels(plan));
  const paren = R.mapOption('Yes (safer)', true);
  check('only a key hint is dropped, not any parenthesis', paren === null, paren);
  check('an empty list is not "translated"', !R.mapDecision({ id: 'e', reason: '', options: [] }, 'zh-TW').translated);
}
console.log('the number sent is the option\'s own');
{
  const d = { id: 'n', reason: 'Do you want to proceed?', options: [{ n: 2, text: 'Yes' }, { n: 5, text: 'No' }] };
  const m = R.mapDecision(d, 'zh-TW');
  check('numbers come from the options, not their places', same(nums(m), [2, 5]) && same(labels(m), ['允許', '不允許']), m.options);
  const u = R.mapDecision({ id: 'u', reason: '?', options: [{ n: 3, text: 'Keep' }, { n: 1, text: 'Drop' }] }, 'en');
  check('…and in the order the CLI listed them', same(nums(u), [3, 1]) && same(labels(u), ['Keep', 'Drop']), u.options);
}

console.log('tool calls');
{
  const calls = [{ tool: 'Read', target: 'a.py' }, { tool: 'Read', target: 'b.py' }, { tool: 'Read', target: 'a.py' },
                 { tool: 'Bash', target: 'npm test' }];
  check('"查了 2 個檔案、跑了 1 個指令" (a file read twice counts once)', R.toolSummary(calls, 'zh-TW') === '查了 2 個檔案、跑了 1 個指令',
    R.toolSummary(calls, 'zh-TW'));
  check('…in English', R.toolSummary(calls, 'en') === 'Looked at 2 files, ran 1 command', R.toolSummary(calls, 'en'));
  const mix = [{ tool: 'Edit', target: 'x.js' }, { tool: 'Grep', target: 'TODO' }, { tool: 'mcp__figma__get_screenshot', target: '' },
               { tool: 'exec_command', target: 'ls' }, { tool: 'apply_patch', target: '' }, { tool: 'Mystery', target: '' }];
  check('Claude, Codex and MCP tools by kind', R.toolSummary(mix, 'zh-TW') === '搜尋了 1 次、改了 2 個檔案、跑了 1 個指令、用了 1 個外部工具、用了 1 個工具',
    R.toolSummary(mix, 'zh-TW'));
}
{
  const turns = [
    { kind: 'user_msg', text: 'fix the build', ts: 1 },
    { kind: 'tool_call', tool: 'Read', target: 'a.py', ts: 2 },
    { kind: 'tool_call', tool: 'Bash', target: 'make', ts: 3 },
    { kind: 'assistant_text', text: 'Found it.', ts: 4 },
    { kind: 'tool_call', tool: 'Edit', target: 'a.py', ts: 5 },
    { kind: 'assistant_text', text: 'Fixed.', ts: 6 },
  ];
  const items = R.buildTimeline(turns);
  check('consecutive calls are one item; text between them splits', same(items.map(i => i.type), ['user', 'tools', 'bot', 'tools', 'bot']), items.map(i => i.type));
  check('…every call kept, in order', same(items[1].calls.map(c => c.tool + ':' + c.target), ['Read:a.py', 'Bash:make']), items[1].calls);
}

console.log('what it is doing');
{
  const zh = st => R.plainDoing(st, 'zh-TW');
  check('Editing relay.py → 正在改 relay.py', same(zh({ state: 'working', activity: { tool: 'Edit', verb: 'Editing', target: 'relay.py' } }),
    { text: '正在改', detail: 'relay.py' }));
  check('Running npm test → 正在跑測試', zh({ state: 'working', activity: { tool: 'Bash', target: 'npm test' } }).text === '正在跑測試');
  check('…from the action line alone', zh({ state: 'working', action: 'Running pytest -q tests/' }).text === '正在跑測試');
  check('Searching src/ → 正在搜尋', same(zh({ state: 'working', action: 'Searching src/' }), { text: '正在搜尋', detail: 'src/' }));
  check('a build', zh({ state: 'working', activity: { tool: 'exec_command', target: 'cargo build --release' } }).text === '正在建置');
  check('a command it does not know', same(zh({ state: 'working', activity: { tool: 'Bash', target: 'osascript -e x' } }),
    { text: '正在執行指令', detail: 'osascript -e x' }));
  check('working with no tool yet: thinking', zh({ state: 'working' }).text === '正在想…');
  check('a question waiting', zh({ state: 'decision' }).text === '等你決定');
  check('done: no live line', zh({ state: 'done' }).text === '');
  check('English', R.plainDoing({ state: 'working', action: 'Running npm test' }, 'en').text === 'Running the tests');
}

console.log('turns');
{
  const compact = { kind: 'user_msg', text: 'This session is being continued from a previous conversation that ran out of context. Summary: …' };
  check("compaction: the phone's own test", R.isCompaction(compact) && !R.isCompaction({ kind: 'user_msg', text: 'hello' })
    && !R.isCompaction({ kind: 'assistant_text', text: compact.text }));
  check('…drawn as a folded note, not as your message', R.buildTimeline([compact])[0].type === 'compact');
  const tg = R.userView('最終要回 Telegram 的文字請放在 [[TG_REPLY_ab12cd34]] 和 [[/TG_REPLY_ab12cd34]] 之間。標記外可以思考或操作，但手機只會收到標記內文字。\n\n幫我看一下報表');
  check("Telegram's marker instruction becomes a tag", tg.text === '幫我看一下報表' && tg.tags[0].kind === 'telegram', tg);
  const a2a = R.userView('[[A2A：來自「研究」的訊息，不是使用者本人。回覆請用 sfctl a2a-result。]]\n資料整理好了');
  check('an A2A header becomes "from 研究"', a2a.text === '資料整理好了' && a2a.tags[0].who === '研究', a2a);
  check("plain text is left alone", same(R.userView('just text [[not a marker]]'), { text: 'just text [[not a marker]]', tags: [] }));
  const codex = [
    { kind: 'user_msg', text: '' },
    { kind: 'assistant_text', text: '' },
    { kind: 'assistant_text', text: '<environment_context>\n  <cwd>/x</cwd>\n</environment_context>' },
    { kind: 'user_msg', text: 'count to 3' },
    { kind: 'assistant_text', text: 'count to 3' },
    { kind: 'assistant_text', text: '1 2 3' },
    { kind: 'assistant_text', text: '1 2 3' },
  ];
  const ci = R.buildTimeline(codex);
  check('Codex: empty records, its context, the echoed prompt and the doubled answer are left out',
    same(ci.map(i => i.type + ':' + i.text), ['user:count to 3', 'bot:1 2 3']), ci);
  const red = [{ kind: 'user_msg', text: '修哪個' },
               { kind: 'assistant_text', text: '兩個都可以。\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知' }];
  check('[[SF:RED]]: the options fold away once they are buttons', R.buildTimeline(red, { foldRed: true })[1].text === '兩個都可以。\n要先修哪個？');
  check('…otherwise only the marker goes', R.buildTimeline(red)[1].text === '兩個都可以。\n要先修哪個？\n1. 先修按鈕\n2. 先修通知');
  check('reply markers and green/working markers are not shown',
    R.botView('[[TG_REPLY_ab12]]好了[[/TG_REPLY_ab12]] [[SF:GREEN]]') === '好了');
  check('a yellow marker reads as a waiting note', R.botView('[[SF:YELLOW:等對方回信]]') === '⏳ 等對方回信');
  check('the call a question is about: the last one since your message',
    same(R.pendingCall([{ kind: 'user_msg', text: 'x' }, { kind: 'tool_call', tool: 'Read', target: 'a' },
                        { kind: 'tool_call', tool: 'Bash', target: 'rm -rf build' }]), { tool: 'Bash', target: 'rm -rf build' })
    && R.pendingCall([{ kind: 'tool_call', tool: 'Bash', target: 'x' }, { kind: 'user_msg', text: 'y' }]) === null);
}

console.log('markdown, safely');
{
  const bad = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '[click](javascript:alert(1))',
    '[x](https://a.com/"onmouseover="alert(1))',
    '```html\n<script>alert(2)</script>\n```',
    '**<b>bold</b>**',
    '| a | <i>b</i> |\n|---|---|\n| <svg onload=alert(3)> | 2 |',
    '> <iframe src="https://evil"></iframe>',
  ];
  for (const s of bad) {
    const html = R.md(s, true);
    // Only the tags md() itself writes, with only its own attributes.
    const tags = html.match(/<[^>]+>/g) || [];
    const allowed = new Set(['p', 'br', 'code', 'pre', 'a', 'strong', 'em', 'del', 'div', 'span', 'button', 'hr', 'blockquote',
                             'table', 'thead', 'tbody', 'tr', 'th', 'td']);
    const stray = tags.filter(tag => {
      const m = tag.match(/^<\/?([a-z][a-z0-9]*)([^>]*)>$/i);
      if (!m || !allowed.has(m[1].toLowerCase())) return true;
      const attrs = m[2].replace(/"[^"]*"/g, '""');              // attribute values may hold anything escaped
      return /\son\w+\s*=/i.test(attrs) || /\s(?:src|style)\s*=/i.test(attrs) && !/^\s*style="--lvl:\d"$/.test(m[2]);
    });
    const badLink = /data-url="(?!https?:)/i.test(html) || /href="(?!#")/i.test(html);
    check(`no HTML from the text: ${s.slice(0, 38).replace(/\n/g, '⏎')}`, !stray.length && !badLink, { stray, html });
  }
  const link = R.md('see [docs](https://example.com/a?b=1&c=2) and https://example.org/x.', false);
  check('http(s) links become links (opened outside the page)', link.includes('data-url="https://example.com/a?b=1&amp;c=2"')
    && link.includes('data-url="https://example.org/x"') && !link.includes('href="http'), link);
  const md = R.md('# Title\n**bold** and `code` and *it*\n\n- one\n  - two\n1. first\n\n| h1 | h2 |\n|---|---|\n| a | b |', true);
  check('the usual markdown', md.includes('cm-h1') && md.includes('<strong>bold</strong>') && md.includes('<code>code</code>')
    && md.includes('<em>it</em>') && md.includes('--lvl:1') && md.includes('>1.</span>') && md.includes('<th>h1</th>'), md);
  check('snake_case is not italic', !R.md('a_b_c and my_var_name', true).includes('<em>'));
  const longCode = '```\n' + Array.from({ length: 40 }, (_, i) => 'line ' + i).join('\n') + '\n```';
  const lc = R.md(longCode, true);
  check('a long code block is kept whole, folded behind "show all"', lc.includes('is-long') && lc.includes('line 39') && lc.includes('顯示全部 40 行'), lc.slice(0, 80));
}

console.log(fails ? `\n${fails} failed, ${passes} passed` : `\nALL PASS (${passes})`);
process.exit(fails ? 1 : 0);
