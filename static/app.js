/* 编程报错检查助手 —— 前端逻辑 */

const $ = (id) => document.getElementById(id);

const el = {
  code: $('code'),
  desc: $('desc'),
  gutter: $('gutter'),
  stdin: $('stdin'),
  language: $('language'),
  sampleSelect: $('sampleSelect'),
  useLLM: $('useLLM'),
  autoFix: $('autoFix'),
  autoTest: $('autoTest'),
  timeout: $('timeout'),
  btnRun: $('btnRun'),
  btnClear: $('btnClear'),
  envBadges: $('envBadges'),
  verdictBar: $('verdictBar'),
  verdictIcon: $('verdictIcon'),
  verdictTitle: $('verdictTitle'),
  verdictSub: $('verdictSub'),
  panelExplain: $('panel-explain'),
  panelRunlog: $('panel-runlog'),
  panelFixed: $('panel-fixed'),
  panelStatic: $('panel-static'),
  panelBoundary: $('panel-boundary'),
  verdictActions: $('verdictActions'),
  toast: $('toast'),
};

let lastResult = null;
let busy = false;

/* ------------------------------------------------------------------ */
/* 工具                                                                */
/* ------------------------------------------------------------------ */
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

let toastTimer = null;
function toast(msg) {
  el.toast.textContent = msg;
  el.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('show'), 2400);
}

function syncGutter() {
  const lines = el.code.value.split('\n').length;
  const nums = [];
  for (let i = 1; i <= Math.max(lines, 1); i++) nums.push(i);
  el.gutter.innerHTML = nums.join('<br>');
  el.gutter.scrollTop = el.code.scrollTop;
}

/* ------------------------------------------------------------------ */
/* 编辑器行为                                                          */
/* ------------------------------------------------------------------ */
el.code.addEventListener('input', syncGutter);
el.code.addEventListener('scroll', () => { el.gutter.scrollTop = el.code.scrollTop; });

el.code.addEventListener('keydown', (e) => {
  // Tab 插入 4 空格
  if (e.key === 'Tab') {
    e.preventDefault();
    const s = el.code.selectionStart, t = el.code.selectionEnd;
    el.code.value = el.code.value.slice(0, s) + '    ' + el.code.value.slice(t);
    el.code.selectionStart = el.code.selectionEnd = s + 4;
    syncGutter();
  }
  // Ctrl/Cmd + Enter 运行
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    runDiagnose();
  }
});

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault();
    runDiagnose();
  }
  if (e.key === 'Escape') {
    closeModals();
  }
});

/* ------------------------------------------------------------------ */
/* Tab 切换                                                            */
/* ------------------------------------------------------------------ */
document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(`panel-${tab.dataset.tab}`).classList.add('active');
  });
});

function switchTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

/* ------------------------------------------------------------------ */
/* 环境与设置                                                          */
/* ------------------------------------------------------------------ */
async function loadEnv() {
  try {
    const r = await fetch('/api/env');
    const d = await r.json();
    const badges = [];

    badges.push(`<span class="badge ok">Python ${escapeHtml(d.python.split(' ')[0])}</span>`);

    if (d.has_c) {
      const name = (d.c_compiler || '').split(/[\\/]/).pop();
      badges.push(`<span class="badge ok">C: ${escapeHtml(name)}</span>`);
    } else {
      badges.push(`<span class="badge bad" title="未检测到 C 编译器，C 代码无法运行">C 编译器缺失</span>`);
    }
    el.envBadges.innerHTML = badges.join('');

    if (!d.has_c) {
      el.verdictSub.textContent = '提示：未检测到 C 编译器，C 语言代码暂时无法运行（Python 正常）。';
    }
  } catch (e) {
    el.envBadges.innerHTML = '<span class="badge bad">服务未连接</span>';
  }
}

async function loadSettings() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    $('setBaseUrl').value = d.base_url || '';
    $('setModel').value = d.model || '';
    $('setApiKey').value = '';
    $('setApiKey').placeholder = d.has_key ? `已保存（${d.key_masked}），留空则不修改` : 'sk-...';
    $('settingsStatus').textContent = d.has_key
      ? `✅ 已配置：${d.model}`
      : '⚠️ 未配置 Key，当前使用内置知识库讲解';
  } catch (e) { /* ignore */ }
}

const PRESETS = {
  deepseek: { url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  qwen: { url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  moonshot: { url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  zhipu: { url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  openai: { url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  ollama: { url: 'http://localhost:11434/v1', model: 'qwen2.5:7b' },
};

document.querySelectorAll('[data-preset]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const p = PRESETS[btn.dataset.preset];
    if (!p) return;
    $('setBaseUrl').value = p.url;
    $('setModel').value = p.model;
    document.querySelectorAll('[data-preset]').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

$('btnSaveSettings').addEventListener('click', async () => {
  const body = {
    base_url: $('setBaseUrl').value.trim(),
    model: $('setModel').value.trim(),
    api_key: $('setApiKey').value,
  };
  const r = await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.ok) {
    toast(d.has_key ? '设置已保存' : '已保存（未配置 Key）');
    await loadSettings();
    closeModals();
  } else {
    toast('保存失败');
  }
});

$('btnClearKey').addEventListener('click', async () => {
  await fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ clear_key: true }),
  });
  toast('已清除 Key');
  await loadSettings();
});

/* ------------------------------------------------------------------ */
/* 弹窗                                                                */
/* ------------------------------------------------------------------ */
function closeModals() {
  document.querySelectorAll('.modal').forEach((m) => m.classList.remove('open'));
}

document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', closeModals));
document.querySelectorAll('.modal').forEach((m) => {
  m.addEventListener('click', (e) => { if (e.target === m) closeModals(); });
});

$('btnSettings').addEventListener('click', () => {
  loadSettings();
  $('modalSettings').classList.add('open');
});

$('btnKnowledge').addEventListener('click', () => {
  $('modalKnowledge').classList.add('open');
  loadKnowledge('all');
});

/* ------------------------------------------------------------------ */
/* 知识库                                                              */
/* ------------------------------------------------------------------ */
let kbCache = null;

async function loadKnowledge(lang) {
  const list = $('kbList');
  if (!kbCache) {
    list.innerHTML = '<p class="muted">加载中...</p>';
    const r = await fetch('/api/knowledge');
    const d = await r.json();
    kbCache = d.entries || [];
  }
  const items = lang === 'all' ? kbCache : kbCache.filter((e) => e.lang === lang);
  if (!items.length) {
    list.innerHTML = '<p class="muted">没有匹配的条目。</p>';
    return;
  }
  list.innerHTML = items.map((e) => `
    <details class="kb-item">
      <summary>${escapeHtml(e.title)}
        <span class="kb-lang">${e.lang === 'python' ? 'Python' : 'C/C++'}</span>
      </summary>
      <div class="kb-body">
        <p>${escapeHtml(e.meaning)}</p>
        ${e.causes && e.causes.length ? `<p><strong>常见原因</strong></p><ul>${e.causes.map((c) => `<li>${escapeHtml(c)}</li>`).join('')}</ul>` : ''}
        ${e.fixes && e.fixes.length ? `<p><strong>怎么修</strong></p><ul>${e.fixes.map((c) => `<li>${escapeHtml(c)}</li>`).join('')}</ul>` : ''}
        ${e.tip ? `<p>💡 ${escapeHtml(e.tip)}</p>` : ''}
      </div>
    </details>
  `).join('');
}

document.querySelectorAll('[data-kb]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-kb]').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    loadKnowledge(btn.dataset.kb);
  });
});

/* ------------------------------------------------------------------ */
/* 示例                                                                */
/* ------------------------------------------------------------------ */
async function loadSamples() {
  try {
    const r = await fetch('/api/samples');
    const d = await r.json();
    d.samples.forEach((s, i) => {
      const opt = document.createElement('option');
      opt.value = String(i);
      opt.textContent = s.title;
      el.sampleSelect.appendChild(opt);
    });
    el.sampleSelect.dataset.samples = JSON.stringify(d.samples);
  } catch (e) { /* ignore */ }
}

el.sampleSelect.addEventListener('change', () => {
  const idx = el.sampleSelect.value;
  if (idx === '') return;
  const samples = JSON.parse(el.sampleSelect.dataset.samples || '[]');
  const s = samples[Number(idx)];
  if (!s) return;
  el.code.value = s.code;
  el.desc.value = s.desc || '';
  el.stdin.value = s.stdin || '';
  el.language.value = s.language || 'auto';
  syncGutter();
  toast(s.note || '示例已加载');
  el.sampleSelect.value = '';
});

/* ------------------------------------------------------------------ */
/* 清空                                                                */
/* ------------------------------------------------------------------ */
el.btnClear.addEventListener('click', () => {
  el.code.value = '';
  el.desc.value = '';
  el.stdin.value = '';
  syncGutter();
  resetResult();
  el.code.focus();
});

function resetResult() {
  el.verdictBar.className = 'verdict-bar';
  el.verdictIcon.textContent = '?';
  el.verdictTitle.textContent = '等待检查';
  el.verdictSub.textContent = '粘贴代码后点击「开始检查代码」';
  el.verdictActions.innerHTML = '';
  lastResult = null;
  el.panelExplain.innerHTML = '<div class="empty"><div class="empty-icon">📖</div><p>这里会显示：这条报错到底在说什么、错在哪一行、该怎么改。</p></div>';
  el.panelRunlog.innerHTML = '<div class="empty"><p>这里会显示真实运行的命令、输出和报错原文。</p></div>';
  el.panelFixed.innerHTML = '<div class="empty"><p>这里会显示修复后的完整代码，以及「重新跑一遍」的验证结果。</p></div>';
  el.panelBoundary.innerHTML = '<div class="empty"><p>代码跑通后，会自动生成极端数据（空数据、0、负数、极大值）再跑一遍，检查健壮性。</p></div>';
  el.panelStatic.innerHTML = '<div class="empty"><p>这里会显示不运行也能发现的低级错误（中文标点、括号不配对等）。</p></div>';
  switchTab('explain');
}

/* ------------------------------------------------------------------ */
/* 运行诊断                                                            */
/* ------------------------------------------------------------------ */
const LOADING_STEPS = [
  '正在你的电脑上真实运行代码…',
  '正在捕获真实报错…',
  '正在拆解报错信息、逐词翻译…',
  '正在生成修复方案…',
  '正在重新运行验证修复结果…',
  '正在生成边界用例并逐个运行…',
];

function showLoading(useLLM, autoFix, autoTest) {
  const steps = LOADING_STEPS.filter((s, i) => {
    if (!useLLM && (i === 2 || i === 3)) return false;
    if (!autoFix && i === 4) return false;
    if (!autoTest && i === 5) return false;
    return true;
  });

  const html = `
    <div class="loading-box">
      <div class="big-spin"></div>
      <div id="loadTitle">${escapeHtml(steps[0] || '处理中…')}</div>
      <div class="loading-steps">
        ${steps.map((s, i) => `<div class="loading-step" data-step="${i}"><span class="step-dot"></span><span>${escapeHtml(s)}</span></div>`).join('')}
      </div>
      <p class="muted small">大模型讲解需要几秒到几十秒，请稍候…</p>
    </div>`;

  el.panelExplain.innerHTML = html;
  el.panelRunlog.innerHTML = '<div class="empty"><p>运行中…</p></div>';
  el.panelFixed.innerHTML = '<div class="empty"><p>运行中…</p></div>';
  el.panelBoundary.innerHTML = '<div class="empty"><p>运行中…</p></div>';
  el.panelStatic.innerHTML = '<div class="empty"><p>运行中…</p></div>';
  switchTab('explain');

  let cur = 0;
  const timer = setInterval(() => {
    if (cur < steps.length) {
      const node = document.querySelector(`.loading-step[data-step="${cur}"]`);
      if (node) {
        node.classList.add('done');
        node.querySelector('.step-dot').textContent = '';
      }
      cur++;
      if (cur < steps.length) {
        const next = document.querySelector(`.loading-step[data-step="${cur}"]`);
        if (next) next.classList.add('active');
        const t = $('loadTitle');
        if (t) t.textContent = steps[cur];
      }
    }
  }, 1100);

  return () => clearInterval(timer);
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    data = null;
  }
  return { status: resp.status, data };
}

/* 裁判接口：优先 /api/evaluate，其次兼容别名。后端版本过旧时返回 404。 */
async function requestJudge(payload) {
  let lastError = '后端没有可用的裁判接口';
  for (const url of ['/api/evaluate', '/api/judge', '/api/check_logic']) {
    try {
      const { status, data } = await postJson(url, payload);
      if (data && data.ok !== false) return data;
      const err = (data && data.error) || `HTTP ${status}`;
      lastError = err;
      if (String(err).includes('接口不存在')) continue;   // 试试下一个别名
      return {
        judged: false,
        judge_source: (data && data.judge_source) || '',
        actual_output: (data && data.actual_output) || '',
        error: err,
        llm_error: (data && (data.llm_error || data.error)) || '',
      };
    } catch (e) {
      lastError = '连接裁判接口失败：' + e.message;
    }
  }
  return { judged: false, error: lastError };
}

async function runDiagnose() {
  if (busy) return;
  const code = el.code.value;
  if (!code.trim()) {
    toast('请先粘贴代码');
    el.code.focus();
    return;
  }

  busy = true;
  el.btnRun.disabled = true;
  el.btnRun.innerHTML = '<span class="spinner"></span> 检查中…';

  el.verdictBar.className = 'verdict-bar warn';
  el.verdictIcon.textContent = '…';
  el.verdictTitle.textContent = '正在检查…';
  el.verdictSub.textContent = '会真实运行你的代码，请稍候';

  const useLLM = el.useLLM.checked;
  const autoFix = el.autoFix.checked;
  const autoTest = el.autoTest.checked;
  const description = el.desc.value.trim();
  const language = el.language.value === 'auto' ? '' : el.language.value;
  const stdin = el.stdin.value;
  const timeout = Number(el.timeout.value) || 10;
  const stopLoading = showLoading(useLLM, autoFix, autoTest);

  if (description) {
    el.verdictSub.textContent = '正在沙盒运行，并调用大模型裁判…';
  }

  try {
    // 有描述时，前端必须单独请求一次裁判接口（LLM），
    // 并让 /api/diagnose 跳过内部裁判，避免调用两次大模型。
    const diagPayload = {
      code,
      description,
      language,
      stdin,
      use_llm: useLLM,
      auto_fix: autoFix,
      auto_test: autoTest,
      timeout,
      skip_judge: !!description,
    };
    const judgePayload = {
      code,
      intent: description,
      language,
      stdin,
      // 只要填了描述，裁判就必须调用大模型，不受「深度讲解」开关影响
      use_llm: true,
      timeout,
    };

    const [diagRes, judgeDataRaw] = await Promise.all([
      postJson('/api/diagnose', diagPayload),
      description ? requestJudge(judgePayload) : Promise.resolve(null),
    ]);

    const d = diagRes.data || { ok: false, error: '服务端没有返回有效数据' };
    let judgeData = d.judge || { judged: false };

    if (description) {
      judgeData = judgeDataRaw || { judged: false, error: '裁判接口没有返回结果' };

      // 兼容旧后端：裁判接口 404 时，退回 /api/diagnose 内部的裁判
      const errText = String(judgeData.error || '');
      if (!judgeData.judged && /接口不存在|没有可用的裁判接口|HTTP 404/.test(errText)) {
        const retry = await postJson('/api/diagnose', { ...diagPayload, skip_judge: false });
        if (retry.data && retry.data.judge && retry.data.judge.judged) {
          Object.assign(d, retry.data);
          judgeData = retry.data.judge;
        }
      }
      if (judgeData.error) console.warn('[judge] 裁判未完成：', judgeData);
    }
    d.judge = judgeData;

    stopLoading();
    if (!d.ok) {
      renderError(d.error || '未知错误');
      return;
    }
    lastResult = d;
    renderResult(d);
  } catch (e) {
    stopLoading();
    renderError('连接服务失败：' + e.message);
  } finally {
    busy = false;
    el.btnRun.disabled = false;
    el.btnRun.innerHTML = '<span class="btn-label">开始检查代码</span>';
  }
}

el.btnRun.addEventListener('click', runDiagnose);

function renderError(msg) {
  el.verdictBar.className = 'verdict-bar error';
  el.verdictIcon.textContent = '!';
  el.verdictTitle.textContent = '出错了';
  el.verdictSub.textContent = msg;
  el.panelExplain.innerHTML = `<div class="empty"><p>${escapeHtml(msg)}</p></div>`;
}

/* ------------------------------------------------------------------ */
/* 渲染结果                                                            */
/* ------------------------------------------------------------------ */
function renderIntent(it, check, runOk) {
  if (!it || !it.provided) return '';

  const c = check || {};
  let cls = 'unk';
  let badge = '❔ 无法自动判断';
  if (c.verdict === 'correct') {
    cls = 'ok'; badge = '✅ 符合你的描述';
  } else if (c.verdict === 'wrong') {
    cls = 'bad'; badge = '❌ 不符合你的描述';
  } else if (!runOk) {
    badge = '❔ 代码还没跑通，暂不判断';
  } else if (!it.task && !it.expected_output) {
    badge = '❔ 描述里没识别出题型';
  }

  const expect = it.expected_output
    ? `<div class="intent-expect">你写的期望输出：<code>${escapeHtml(it.expected_output)}</code></div>`
    : '';
  const fmts = (it.formats || [])
    .map((f) => `<span class="intent-fmt">${escapeHtml(f)}</span>`).join('');
  const reasons = (c.reasons || []).length
    ? `<ul class="intent-reasons">${c.reasons.map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>`
    : '';

  return `<div class="intent-card ${cls}">
    <div class="exp-head">
      <span class="log-title" style="margin:0">按你的描述判断${it.task_label ? '：' + escapeHtml(it.task_label) : ''}</span>
      <span class="exp-badge ${cls}">${badge}</span>
    </div>
    <div class="intent-desc">你说的作用：“${escapeHtml(it.description)}”</div>
    ${expect}
    ${reasons}
    ${fmts ? `<div>${fmts}</div>` : ''}
  </div>`;
}

function renderJudgePanel(j) {
  const mock = j.judge_source === 'mock';

  if (!j.judged) {
    const msg = j.error || j.llm_error || '未知原因';
    const noKey = /未配置|API Key|Mock 模拟/i.test(msg);
    const tag = noKey ? '[当前为无 Key 测试模式] ' : '[大模型裁判未执行] ';
    const raw = j.llm_error && j.llm_error !== msg
      ? `<div class="judge-meta">原始错误：${escapeHtml(j.llm_error)}</div>` : '';
    const output = j.actual_output
      ? `<div class="judge-row"><span class="judge-key">真实输出</span><code class="judge-out">${escapeHtml(j.actual_output.trim())}</code></div>`
      : '';
    return `<div class="judge-panel unk">
      <div class="judge-banner">${tag}${escapeHtml(msg)}</div>
      ${output}
      ${raw}
      <p class="muted" style="margin-top:8px">${noKey
        ? '配置 API Key 后会自动进行真实逻辑判断。'
        : '请检查 API Key、网络或模型配置；以上为接口返回的原始错误。'}</p>
    </div>`;
  }

  const logicOk = !!j.is_logic_correct;
  const mockBanner = mock
    ? '<div class="judge-banner mock">[当前为无 Key 测试模式] 以下为 Mock 测试数据（未调用真实大模型）；配置 API Key 后自动切换为真实判定。</div>'
    : '';

  return `<div class="judge-panel ${logicOk ? 'ok' : 'bad'}">
    ${mockBanner}
    <div class="judge-head">
      <span class="judge-title">大模型裁判（LLM as a Judge）</span>
      <span class="judge-badge ${logicOk ? 'ok' : 'bad'}">${logicOk ? '✅ 逻辑正确' : '❌ 逻辑错误'}</span>
    </div>
    <div class="judge-row"><span class="judge-key">你的描述</span><span>${escapeHtml(j.intent || '')}</span></div>
    <div class="judge-row"><span class="judge-key">真实输出</span><code class="judge-out">${escapeHtml((j.actual_output || '（无输出）').trim())}</code></div>
    <div class="judge-reason"><span class="judge-key">裁判理由</span>${escapeHtml(j.reason || j.feedback || '')}</div>
    <div class="judge-meta">判定来源：${mock ? 'Mock 模拟' : '真实大模型'} · verdict = ${escapeHtml(j.verdict || '')}${j.verdict_corrected ? ' · 已按理由自动纠正' : ''}</div>
  </div>`;
}

function renderResult(d) {
  const run = d.first_run || {};
  const stage = run.stage || '';
  const ok = !!run.ok;

  // ---- 顶部结论 ----
  const exp = d.expected || {};
  let cls = 'warn', icon = '!', title = d.verdict || '', sub = '';
  if (ok) {
    cls = 'success'; icon = '✓';
    sub = '代码没有报错，可以直接使用';
    if (exp.matches === true) {
      sub = `输出已验证正确 · 正确答案 ${exp.display}`;
    } else if (exp.matches === false) {
      cls = 'warn'; icon = '!';
      sub = `能跑通但结果算错了 · 正确答案应为 ${exp.display}`;
    }
    const ic = d.intent_check || {};
    if (d.intent_mismatch) {
      cls = 'warn'; icon = '!';
      sub = ic.short || '按你的描述核对没有通过';
    } else if (ic.verdict === 'correct') {
      sub = exp.matches === true
        ? `${ic.short} · 正确答案 ${exp.display}`
        : (ic.short || sub);
    }

    // 大模型裁判（唯一逻辑判定来源）优先展示；Mock 模式也必须显示判定
    const jg = d.judge || {};
    if (jg.judged) {
      const mockTag = jg.judge_source === 'mock' ? '[当前为无 Key 测试模式] ' : '';
      cls = jg.is_logic_correct ? 'success' : 'error';
      icon = jg.is_logic_correct ? '✓' : '!';
      title = jg.is_logic_correct ? '大模型裁判：逻辑正确' : '大模型裁判：逻辑错误';
      sub = `${mockTag}${jg.is_logic_correct ? '输出符合你的描述' : '输出与你的描述不符'}`;
    } else if (jg.error) {
      cls = 'warn'; icon = '!';
      title = '大模型裁判：未执行';
      sub = jg.error;
    }
  } else if (stage === 'env_error') {
    cls = 'warn'; icon = '⚙';
    sub = '缺少编译器/解释器';
  } else if (d.fixed_ok) {
    cls = 'warn'; icon = '✓';
    sub = '已自动修复并验证跑通，见「修复代码」';
  } else if (stage === 'compile_error') {
    cls = 'error'; icon = '×';
    sub = `${d.language_label} 编译失败 · 程序一行都没跑起来`;
  } else if (stage === 'syntax_error') {
    cls = 'error'; icon = '×';
    sub = '语法错误 · 程序一个字都没执行';
  } else if (stage === 'timeout') {
    cls = 'error'; icon = '⏱';
    sub = '运行超时';
  } else {
    cls = 'error'; icon = '×';
    sub = `${d.language_label} 运行时崩溃 · 退出码 ${run.exit_code}`;
  }
  if (!sub) sub = `${d.language_label} · ${stage}`;

  el.verdictBar.className = 'verdict-bar ' + cls;
  el.verdictIcon.textContent = icon;
  el.verdictTitle.textContent = title;
  el.verdictSub.textContent = sub;

  // ---- 讲解 ----
  // 有裁判结果（含 Mock）时，直接展示裁判面板；
  // 不再渲染旧的 expected / 规则解释，避免出现「没能判断出这段程序在算什么」。
  const judge = d.judge || {};
  const explainMd = d.explanation_html
    ? `<div class="md" style="margin-top:14px">${d.explanation_html}</div>`
    : '';
  if (judge.judged || judge.error) {
    el.panelExplain.innerHTML = renderJudgePanel(judge) + explainMd;
  } else {
    el.panelExplain.innerHTML = renderIntent(d.intent, d.intent_check, ok)
      + `<div class="md">${d.explanation_html || '<p>（无内容）</p>'}</div>`;
  }
  addCopyButtons(el.panelExplain);

  // ---- 运行现场 ----
  el.panelRunlog.innerHTML = renderRunlog(d);
  addCopyButtons(el.panelRunlog);

  // ---- 修复代码 ----
  el.panelFixed.innerHTML = renderFixed(d);
  addCopyButtons(el.panelFixed);

  // ---- 边界测试 ----
  el.panelBoundary.innerHTML = renderBoundary(d);

  // ---- 静态预检 ----
  el.panelStatic.innerHTML = renderStatic(d);

  renderCollectButton();

  // 默认展示哪个 tab
  if (d.boundary_failed > 0) switchTab('boundary');
  else if (!ok && d.fixed_ok) switchTab('fixed');
  else switchTab('explain');
}

const KIND_LABEL = {
  compile_error: '编译错误',
  compile_warning: '编译警告',
  compile_note: '编译提示',
  compile_fatal: '致命错误',
};

const STAGE_LABEL = {
  success: '运行成功',
  runtime_error: '运行时崩溃',
  syntax_error: '语法错误（未执行）',
  compile_error: '编译失败',
  timeout: '运行超时',
  env_error: '环境缺失',
};

function renderExpectedCard(exp) {
  if (!exp || (!exp.available && !exp.note)) return '';
  if (!exp.available) {
    return `<div class="log-block"><div class="log-title">正确输出</div>
      <pre>${escapeHtml(exp.note || '无法自动推算')}</pre></div>`;
  }
  const state = exp.matches;
  const badge = state === true
    ? '<span class="exp-badge ok">✅ 一致</span>'
    : state === false
      ? '<span class="exp-badge bad">❌ 不一致</span>'
      : '<span class="exp-badge unk">❔ 需人工核对</span>';

  return `<div class="exp-card ${state === true ? 'ok' : state === false ? 'bad' : 'unk'}">
    <div class="exp-head">
      <span class="log-title" style="margin:0">正确输出（用独立参考实现重算）</span>
      ${badge}
    </div>
    ${exp.formula ? `<div class="exp-formula">${escapeHtml(exp.formula)}</div>` : ''}
    <div class="exp-answer">正确答案：<strong>${escapeHtml(exp.display)}</strong></div>
    ${exp.actual && exp.actual.trim()
      ? `<div class="exp-actual">你的程序输出：<code>${escapeHtml(exp.actual.trim().slice(0, 300))}</code></div>`
      : '<div class="exp-actual">你的程序输出：（无）</div>'}
    ${exp.note ? `<div class="exp-note">${escapeHtml(exp.note)}</div>` : ''}
  </div>`;
}

function renderRunlog(d) {
  const run = d.first_run || {};
  const parsed = d.parsed || {};
  const issues = parsed.issues || [];
  const parts = [];

  // 有裁判结果时不再显示旧的「正确输出/无法推算」卡片
  const jg = d.judge || {};
  const expCard = (jg.judged || jg.error) ? '' : renderExpectedCard(d.expected);
  if (expCard) parts.push(expCard);

  if (run.env_hint) {
    parts.push(`<div class="log-block"><div class="log-title">编译器信息</div><pre>${escapeHtml(run.env_hint)}</pre></div>`);
  }

  if (run.command) {
    parts.push(`<div class="log-block"><div class="log-title">执行的命令</div><pre>${escapeHtml(run.command)}</pre></div>`);
  }

  parts.push(`<div class="log-block"><div class="log-title">运行结果</div>
    <pre>阶段：${escapeHtml(STAGE_LABEL[run.stage] || run.stage || '')}
退出码：${run.exit_code === null || run.exit_code === undefined ? '—' : run.exit_code}
耗时：${run.duration || 0} 秒</pre></div>`);

  if (run.stdout && run.stdout.trim()) {
    parts.push(`<div class="log-block out"><div class="log-title">标准输出 stdout</div><pre>${escapeHtml(run.stdout)}</pre></div>`);
  }

  if (run.stderr && run.stderr.trim()) {
    parts.push(`<div class="log-block err"><div class="log-title">错误输出 stderr（这就是真实报错）</div><pre>${escapeHtml(run.stderr)}</pre></div>`);
  }

  if (issues.length) {
    const rows = issues.slice(0, 10).map((it) => {
      const loc = it.line ? `第 ${it.line} 行` + (it.column ? ` 第 ${it.column} 列` : '') : '—';
      return `<tr>
        <td>${escapeHtml(it.severity === 'warning' ? '警告' : '错误')}</td>
        <td><code class="inline">${escapeHtml(KIND_LABEL[it.kind] || it.kind)}</code></td>
        <td>${escapeHtml(loc)}</td>
        <td>${escapeHtml(it.message || '')}</td>
      </tr>`;
    }).join('');
    parts.push(`
      <div class="log-block">
        <div class="log-title">解析出的错误清单</div>
        <table class="md-table">
          <thead><tr><th>级别</th><th>类型</th><th>位置</th><th>消息</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`);
  }

  if (d.llm_error) {
    parts.push(`<div class="log-block"><div class="log-title">AI 状态</div><pre>${escapeHtml(d.llm_error)}</pre></div>`);
  }

  if (d.attempts && d.attempts.length) {
    const items = d.attempts.map((a) => `
      <tr>
        <td>第 ${a.round} 次</td>
        <td>${a.ok ? '✅ 通过' : '❌ 仍有报错'}</td>
        <td>${escapeHtml((a.run && a.run.stage) || '')}</td>
      </tr>`).join('');
    parts.push(`
      <div class="log-block">
        <div class="log-title">自动修复验证记录</div>
        <table class="md-table">
          <thead><tr><th>轮次</th><th>结果</th><th>阶段</th></tr></thead>
          <tbody>${items}</tbody>
        </table>
      </div>`);
  }

  if (!parts.length) return '<div class="empty"><p>没有运行记录。</p></div>';
  return parts.join('');
}

function renderFixed(d) {
  const parts = [];
  const fence = (d.language === 'c' || d.language === 'cpp') ? 'c' : 'python';

  if (!d.fixed_code) {
    parts.push(`<div class="empty">
      <div class="empty-icon">🔧</div>
      <p>${d.fixed_ok ? '原代码已经能跑通，无需修复。' : '没有生成修复代码。<br>勾选「自动修复并验证」（并配置 AI）后即可自动修复。'}</p>
    </div>`);
    return parts.join('');
  }

  const verified = d.fixed_ok;
  parts.push(`
    <div class="issue-card" style="border-left-color:${verified ? 'var(--success)' : 'var(--warn)'}">
      <div class="issue-head">
        <span class="issue-title">${verified ? '✅ 已验证：这段代码重新运行通过' : '⚠️ 未通过验证：这段代码重新运行仍报错'}</span>
      </div>
      <div class="issue-detail">${verified
        ? '这是在你电脑上真实运行过的代码，不是纸上谈兵。'
        : '请查看「运行现场」的最新报错，或把它再发一次继续排查。'}</div>
    </div>`);

  parts.push(`<div class="code-block-wrap">
    <pre class="code" style="background:#080b11;border:1px solid var(--border);border-radius:8px;padding:12px 14px;overflow-x:auto;margin:10px 0;">
<code>${escapeHtml(d.fixed_code)}</code></pre>
    <button class="copy-btn" data-copy>复制代码</button>
  </div>`);

  if (verified && d.fixed_run) {
    const fr = d.fixed_run;
    if (fr.stdout && fr.stdout.trim()) {
      parts.push(`<div class="log-block out"><div class="log-title">修复后程序的输出</div><pre>${escapeHtml(fr.stdout)}</pre></div>`);
    } else {
      parts.push(`<div class="log-block out"><div class="log-title">修复后程序的输出</div><pre>（程序正常结束，没有任何输出）</pre></div>`);
    }
  }

  return parts.join('');
}

const BD_ICON = { pass: '✅', fail: '❌', warn: '⚠️', unknown: '❔' };
const BD_LABEL = { pass: '通过', fail: '崩溃', warn: '需留意', unknown: '待确认' };

function renderBoundary(d) {
  const bd = d.boundary || {};
  const cases = bd.cases || [];

  if (!cases.length) {
    const mainOk = (d.first_run || {}).ok;
    if (!mainOk) {
      return `<div class="empty">
        <div class="empty-icon">🧪</div>
        <p>代码本身还没跑通，边界测试要等主错误修好之后才有意义。<br>
        <span class="muted">把上面的报错解决掉，再检查一次就会自动执行。</span></p>
      </div>`;
    }
    return `<div class="empty">
      <div class="empty-icon">🧪</div>
      <p>本次没有执行边界测试。<br>
      <span class="muted">勾选左侧「自动边界测试」后重新检查即可。</span></p>
    </div>`;
  }

  const profile = bd.profile || {};
  const spec = profile.spec || {};
  const s = bd.summary || {};

  const head = `
    <div class="bd-head">
      <div class="bd-meta">
        <span class="bd-chip">识别为：${escapeHtml(profile.task_label || '通用程序')}</span>
        <span class="bd-chip">置信度 ${escapeHtml(profile.confidence || 'low')}</span>
        <span class="bd-chip">输入：${escapeHtml(spec.desc || '未知')}</span>
      </div>
      <div class="bd-stats">
        <span class="bd-stat pass">通过 ${s.pass || 0}</span>
        <span class="bd-stat fail">崩溃 ${s.fail || 0}</span>
        <span class="bd-stat warn">需留意 ${s.warn || 0}</span>
        ${s.unknown ? `<span class="bd-stat unknown">待确认 ${s.unknown}</span>` : ''}
      </div>
      <p class="bd-conclusion">${escapeHtml(bd.conclusion || '')}</p>
      ${(profile.notes || []).map((n) => `<p class="bd-note">💡 ${escapeHtml(n)}</p>`).join('')}
    </div>`;

  const items = cases.map((c) => {
    const status = c.status || 'unknown';
    const stdinShow = c.stdin ? c.stdin.replace(/\n/g, ' ⏎ ').trim() : '（空）';
    const showAdvice = status === 'fail' || status === 'warn';

    return `
    <div class="bd-case ${status}">
      <div class="bd-case-head">
        <span class="bd-icon">${BD_ICON[status] || '❔'}</span>
        <span class="bd-name">${escapeHtml(c.name)}</span>
        <span class="bd-status ${status}">${BD_LABEL[status] || status}</span>
        ${c.duration ? `<span class="bd-dur">${c.duration}s</span>` : ''}
      </div>
      <div class="bd-stdin"><span class="bd-key">输入</span><code>${escapeHtml(stdinShow)}</code></div>
      <div class="bd-why"><span class="bd-key">为什么测它</span>${escapeHtml(c.why || '')}</div>
      ${c.expect ? `<div class="bd-why"><span class="bd-key">期望</span>${escapeHtml(c.expect)}</div>` : ''}
      ${showAdvice && c.reason ? `<div class="bd-reason">${escapeHtml(c.reason)}</div>` : ''}
      ${showAdvice && c.advice ? `<div class="bd-advice"><span class="bd-key">怎么改</span>${escapeHtml(c.advice)}</div>` : ''}
      ${c.stdout && c.stdout.trim() ? `<pre class="bd-out">输出：${escapeHtml(c.stdout.trim().slice(0, 400))}</pre>` : ''}
    </div>`;
  }).join('');

  return `<div class="bd-wrap">${head}${items}</div>`;
}

function renderStatic(d) {
  const issues = d.static_issues || [];
  if (!issues.length) {
    return `<div class="empty"><div class="empty-icon">✨</div><p>静态预检没有发现问题。<br><span class="muted">（中文标点、括号配对、漏分号、缺少头文件、scanf 忘 & 等）</span></p></div>`;
  }
  return issues.map((s) => `
    <div class="issue-card ${s.severity === 'warning' ? 'warning' : ''}">
      <div class="issue-head">
        <span class="issue-line">第 ${s.line} 行</span>
        <span class="issue-title">${escapeHtml(s.title)}</span>
      </div>
      ${s.code ? `<div class="issue-detail" style="font-family:var(--mono);font-size:12px;color:#8ea3bf;">${escapeHtml(s.code)}</div>` : ''}
      <div class="issue-detail">${escapeHtml(s.detail)}</div>
      <div class="issue-fix">${escapeHtml(s.fix)}</div>
    </div>`).join('');
}

/* ------------------------------------------------------------------ */
/* 代码块复制按钮                                                      */
/* ------------------------------------------------------------------ */
function addCopyButtons(container) {
  container.querySelectorAll('pre').forEach((pre) => {
    if (pre.parentElement.classList.contains('code-block-wrap')) return;
    const wrap = document.createElement('div');
    wrap.className = 'code-block-wrap';
    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(pre);

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = '复制';
    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(pre.innerText);
        btn.textContent = '已复制';
        setTimeout(() => { btn.textContent = '复制'; }, 1500);
      } catch (e) {
        toast('复制失败，请手动选中复制');
      }
    });
    wrap.appendChild(btn);
  });
}

/* ------------------------------------------------------------------ */
/* 错题集                                                              */
/* ------------------------------------------------------------------ */
let mkCache = [];
let mkFilter = 'all';
let pendingDiagnosis = null;

async function refreshMkCount() {
  try {
    const r = await fetch('/api/mistakes');
    const d = await r.json();
    const pill = $('mkCountPill');
    if (!pill) return;
    const todo = (d.total || 0) - (d.mastered || 0);
    pill.textContent = String(todo);
    pill.classList.toggle('zero', todo === 0);
  } catch (e) { /* ignore */ }
}

function renderCollectButton() {
  const box = el.verdictActions;
  if (!box) return;
  if (!lastResult) { box.innerHTML = ''; return; }

  box.innerHTML = '<button class="btn ghost small" id="btnCollect" title="把这次的代码和报错存下来">☆ 收进错题集</button>';
  const btn = $('btnCollect');
  if (!btn) return;
  btn.addEventListener('click', () => {
    pendingDiagnosis = lastResult;
    $('collectNote').value = '';
    $('collectTags').value = '';
    $('modalCollect').classList.add('open');
    setTimeout(() => $('collectNote').focus(), 120);
  });
}

$('btnConfirmCollect').addEventListener('click', async () => {
  if (!pendingDiagnosis) {
    toast('没有可收藏的内容');
    return;
  }
  const btn = $('btnConfirmCollect');
  btn.disabled = true;
  btn.textContent = '收藏中…';

  try {
    const diag = Object.assign({}, pendingDiagnosis);
    delete diag.explanation_html;   // 后端会重新渲染，不用传
    delete diag.boundary;           // 错题集只存本题信息，不存边界测试明细

    const r = await fetch('/api/mistakes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        diagnosis: diag,
        note: $('collectNote').value.trim(),
        tags: $('collectTags').value.trim(),
      }),
    });
    const d = await r.json();
    if (d.ok) {
      closeModals();
      toast(d.duplicated ? '这道题已在错题集中，笔记已合并' : '已收进错题集，可以随时回来复习');
      refreshMkCount();
    } else {
      toast(d.error || '收藏失败');
    }
  } catch (e) {
    toast('收藏失败：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '确认收藏';
  }
});

$('btnMistakes').addEventListener('click', () => {
  $('modalMistakes').classList.add('open');
  loadMistakes();
});

document.querySelectorAll('[data-mk]').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('[data-mk]').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    mkFilter = btn.dataset.mk;
    renderMkList();
  });
});

async function loadMistakes() {
  const list = $('mkList');
  list.innerHTML = '<p class="muted">加载中…</p>';
  try {
    const r = await fetch('/api/mistakes');
    const d = await r.json();
    mkCache = d.items || [];
    $('mkCount').textContent = d.total
      ? `共 ${d.total} 道 · 待复习 ${d.total - d.mastered} 道 · 已掌握 ${d.mastered} 道`
      : '';
    renderMkList();
    refreshMkCount();
  } catch (e) {
    list.innerHTML = '<p class="muted">加载失败</p>';
  }
}

function mkCard(it) {
  const time = (it.created_at || '').slice(5, 16).replace('T', ' ');
  const line = it.error_line ? `第 ${it.error_line} 行` : '';
  const status = it.mastered
    ? '<span class="mk-status done">已掌握</span>'
    : '<span class="mk-status todo">待复习</span>';
  const tags = (it.tags || []).map((t) => `<span class="mk-tag">${escapeHtml(t)}</span>`).join('');
  const codeFirst = (it.code || '').split('\n').slice(0, 3).join('\n');

  return `
  <div class="mk-item ${it.mastered ? 'is-done' : ''}" data-id="${escapeHtml(it.id)}">
    <div class="mk-head">
      <span class="mk-lang">${escapeHtml(it.language_label || it.language || '')}</span>
      <span class="mk-kind">${escapeHtml(KIND_LABEL[it.error_kind] || it.error_kind || '错误')}</span>
      ${line ? `<span class="mk-line-tag">${escapeHtml(line)}</span>` : ''}
      <span class="mk-time">${escapeHtml(time)}</span>
      ${status}
    </div>
    <div class="mk-title">${escapeHtml(it.knowledge_title || it.error_message || '')}</div>
    <pre class="mk-snippet">${escapeHtml(codeFirst)}</pre>
    ${tags ? `<div class="mk-tags">${tags}</div>` : ''}
    <div class="mk-btns">
      <button class="btn ghost small" data-act="view">查看详情</button>
      <button class="btn ghost small" data-act="master">${it.mastered ? '标记为待复习' : '标记已掌握'}</button>
      <button class="btn ghost small danger" data-act="del">删除</button>
    </div>
  </div>`;
}

function renderMkList() {
  const list = $('mkList');
  let items = mkCache;
  if (mkFilter === 'todo') items = items.filter((x) => !x.mastered);
  if (mkFilter === 'done') items = items.filter((x) => x.mastered);

  if (!items.length) {
    list.innerHTML = `<div class="empty">
      <div class="empty-icon">📚</div>
      <p>${mkCache.length ? '这个分类下没有题目。' : '错题集还是空的。<br>检查完代码后，点右上角「☆ 收进错题集」把这次的错误存下来。'}</p>
    </div>`;
    return;
  }
  list.innerHTML = items.map(mkCard).join('');
}

/* 事件委托：只绑一次 */
$('mkList').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-act]');
  if (!btn) return;
  const card = btn.closest('.mk-item');
  if (!card) return;
  const id = card.dataset.id;
  const act = btn.dataset.act;

  if (act === 'view') {
    toggleMkDetail(card, id, btn);
    return;
  }

  if (act === 'master') {
    const item = mkCache.find((x) => x.id === id);
    if (!item) return;
    const next = !item.mastered;
    const r = await fetch('/api/mistakes/' + encodeURIComponent(id), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mastered: next, mark_reviewed: true }),
    });
    const d = await r.json();
    if (d.ok) {
      item.mastered = next;
      item.review_count = (item.review_count || 0) + 1;
      renderMkList();
      refreshMkCount();
      toast(next ? '已标记为掌握 👍' : '已放回待复习');
    } else {
      toast('操作失败');
    }
    return;
  }

  if (act === 'del') {
    if (btn.dataset.confirm !== '1') {
      btn.dataset.confirm = '1';
      btn.textContent = '再点一次确认删除';
      btn.classList.add('danger-solid');
      setTimeout(() => {
        if (btn.dataset.confirm === '1') {
          btn.dataset.confirm = '0';
          btn.textContent = '删除';
          btn.classList.remove('danger-solid');
        }
      }, 3000);
      return;
    }
    const r = await fetch('/api/mistakes/' + encodeURIComponent(id), { method: 'DELETE' });
    const d = await r.json();
    if (d.ok) {
      mkCache = mkCache.filter((x) => x.id !== id);
      renderMkList();
      refreshMkCount();
      toast('已删除');
    } else {
      toast('删除失败');
    }
    return;
  }

  if (act === 'save-note') {
    const detail = card.querySelector('.mk-detail');
    const note = detail ? detail.querySelector('.mk-note') : null;
    const r = await fetch('/api/mistakes/' + encodeURIComponent(id), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note: note ? note.value : '' }),
    });
    const d = await r.json();
    toast(d.ok ? '笔记已保存' : '保存失败');
    if (d.ok) {
      const item = mkCache.find((x) => x.id === id);
      if (item) item.note = note ? note.value : '';
    }
    return;
  }
});

async function toggleMkDetail(card, id, btn) {
  const existing = card.querySelector('.mk-detail');
  if (existing) {
    existing.remove();
    btn.textContent = '查看详情';
    return;
  }

  btn.textContent = '加载中…';
  try {
    const r = await fetch('/api/mistakes/' + encodeURIComponent(id));
    const d = await r.json();
    if (!d.ok) {
      toast('加载失败');
      btn.textContent = '查看详情';
      return;
    }
    const it = d.item;
    const div = document.createElement('div');
    div.className = 'mk-detail';
    div.innerHTML = `
      <div class="mk-section">
        <div class="mk-label">当时的代码</div>
        <pre class="mk-pre">${escapeHtml(it.code || '')}</pre>
      </div>
      ${it.stderr ? `<div class="mk-section">
        <div class="mk-label">真实报错</div>
        <pre class="mk-pre err">${escapeHtml((it.stderr || '').trim())}</pre>
      </div>` : ''}
      ${it.fixed_code ? `<div class="mk-section">
        <div class="mk-label">${it.fixed_ok ? '正确写法' : (it.fixed_from === 'knowledge' ? '参考写法（同类错误的通用示例）' : '正确写法')}
          ${it.fixed_ok ? '<span class="ok-tag">已验证跑通</span>' : ''}</div>
        <pre class="mk-pre good">${escapeHtml(it.fixed_code)}</pre>
      </div>` : ''}
      ${it.explanation_html ? `<div class="mk-section">
        <div class="mk-label">当时的讲解</div>
        <div class="md mk-md">${it.explanation_html}</div>
      </div>` : ''}
      <div class="mk-section">
        <div class="mk-label">我的笔记</div>
        <textarea class="modal-textarea mk-note" rows="2" placeholder="记点什么，下次别再犯…">${escapeHtml(it.note || '')}</textarea>
        <button class="btn ghost small" data-act="save-note">保存笔记</button>
      </div>
      ${it.review_count ? `<div class="mk-reviewed">已复习 ${it.review_count} 次</div>` : ''}
    `;
    card.appendChild(div);
    addCopyButtons(div);
    btn.textContent = '收起详情';
  } catch (e) {
    toast('加载失败');
    btn.textContent = '查看详情';
  }
}

$('btnExport').addEventListener('click', () => {
  const a = document.createElement('a');
  a.href = '/api/mistakes/export';
  a.download = 'mistakes.md';
  document.body.appendChild(a);
  a.click();
  a.remove();
  toast('正在导出错题本');
});

$('btnClearMastered').addEventListener('click', async () => {
  const done = mkCache.filter((x) => x.mastered).length;
  if (!done) {
    toast('还没有已掌握的题目');
    return;
  }
  const btn = $('btnClearMastered');
  if (btn.dataset.confirm !== '1') {
    btn.dataset.confirm = '1';
    btn.textContent = `确认清空 ${done} 道？`;
    setTimeout(() => {
      if (btn.dataset.confirm === '1') {
        btn.dataset.confirm = '0';
        btn.textContent = '清空已掌握';
      }
    }, 3000);
    return;
  }
  const r = await fetch('/api/mistakes/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ only_mastered: true }),
  });
  const d = await r.json();
  btn.dataset.confirm = '0';
  btn.textContent = '清空已掌握';
  if (d.ok) {
    toast(`已清空 ${d.removed} 道`);
    loadMistakes();
  } else {
    toast('操作失败');
  }
});

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
(async function init() {
  syncGutter();
  await Promise.all([loadEnv(), loadSettings(), loadSamples()]);
  refreshMkCount();
})();
