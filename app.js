let shares = [];
let toastTimer;
let adminToken = sessionStorage.getItem('lan-file-hub-admin-token') || '';
const collapsedShares = new Set();
const fileCategories = [
  { label: '文件夹', color: '#607d6e', extensions: [] },
  { label: '文档', color: '#3f82ba', extensions: ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'csv', 'rtf', 'odt'] },
  { label: '图片', color: '#da8a45', extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'heic'] },
  { label: '视频', color: '#9d6bb5', extensions: ['mp4', 'mov', 'avi', 'mkv', 'wmv', 'flv', 'webm'] },
  { label: '音频', color: '#d06671', extensions: ['mp3', 'wav', 'flac', 'aac', 'm4a', 'ogg'] },
  { label: '压缩包', color: '#c29b39', extensions: ['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'] },
  { label: '代码', color: '#4c9b79', extensions: ['js', 'ts', 'py', 'java', 'c', 'cpp', 'cs', 'go', 'rs', 'html', 'css', 'json', 'xml', 'sql'] },
  { label: '其他', color: '#87938e', extensions: [] },
];

function fileExtension(name) { const extension = name.split('.').pop(); return extension === name ? 'FILE' : extension.slice(0, 4).toUpperCase(); }
function categoryForFile(file) { if (file.kind === 'directory') return fileCategories[0]; const extension = file.name.split('.').pop().toLowerCase(); return fileCategories.find(category => category.extensions.includes(extension)) || fileCategories[fileCategories.length - 1]; }
function escapeHTML(value) { return String(value ?? '').replace(/[&<>'"]/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', "'":'&#39;', '"':'&quot;' }[char])); }
function downloadPath(share, file) { return `/api/files/${encodeURIComponent(share.id)}/download?path=${encodeURIComponent(file.path)}`; }
function toast(message) { const el = document.querySelector('#toast'); el.textContent = message; el.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove('show'), 2800); }

async function request(url, options = {}) {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(adminToken ? { 'X-Admin-Token': adminToken } : {}), ...(options.headers || {}) };
  const response = await fetch(url, { ...options, headers });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(result.error || '请求失败，请稍后重试。'); error.status = response.status; throw error; }
  return result;
}

async function loadShares() {
  try { const result = await request('/api/shares'); shares = result.shares; render(); }
  catch (error) { document.querySelector('#share-groups').innerHTML = ''; document.querySelector('#empty-files').classList.remove('hidden'); document.querySelector('#empty-files').querySelector('h2').textContent = '无法连接文件服务'; document.querySelector('#empty-files').querySelector('p').textContent = '请通过内网服务地址访问此页面，并确认服务器正在运行。'; toast(error.message); }
}

function renderFiles() {
  const query = document.querySelector('#file-search').value.trim().toLowerCase();
  const groups = shares.map(share => ({ ...share, files: share.files.filter(file => `${file.path} ${share.name}`.toLowerCase().includes(query)) })).filter(share => share.files.length || !query);
  const count = groups.reduce((total, share) => total + share.files.length, 0);
  document.querySelector('#file-count').textContent = `${count} 个一级项目 · ${groups.length} 个位置`;
  document.querySelector('#share-groups').innerHTML = groups.map(share => {
    const collapsed = collapsedShares.has(share.id);
    return `<article class="share-group ${share.available ? '' : 'unavailable'} ${collapsed ? 'collapsed' : ''}">
      <header class="group-header"><div class="group-name"><span class="folder-icon">▰</span><div><div class="group-title">${escapeHTML(share.name)}</div><div class="group-path">${escapeHTML(share.networkPath)}</div></div></div><div class="group-controls"><span class="file-meta">${share.available ? `${share.files.length} 个一级项目` : '服务器暂不可访问'}</span><button class="group-toggle" type="button" data-toggle-share="${escapeHTML(share.id)}" aria-expanded="${!collapsed}" aria-label="${collapsed ? '展开文件夹' : '收起文件夹'}" title="${collapsed ? '展开' : '收起'}"><span class="chevron" aria-hidden="true"></span></button></div></header>
      <div class="group-content">${share.available && share.files.length ? `<table class="file-table"><thead><tr><th>名称</th><th>类型</th><th>大小</th><th></th></tr></thead><tbody>${share.files.map(file => `<tr><td><div class="file-info"><span class="file-icon">${file.kind === 'directory' ? 'DIR' : fileExtension(file.name)}</span><div><span class="file-name">${escapeHTML(file.name)}</span></div></div></td><td class="file-meta">${file.kind === 'directory' ? '文件夹' : `${fileExtension(file.name)} 文件`}</td><td class="file-meta">${escapeHTML(file.size)}</td><td><a class="download-link" href="${downloadPath(share, file)}">${file.kind === 'directory' ? '下载文件夹' : '下载'} <span>↓</span></a></td></tr>`).join('')}</tbody></table>` : `<div class="group-empty">${share.available ? '此文件夹中暂未发现一级项目。' : '请检查 B 机器的网络、共享名与 SMB 账号权限。'}</div>`}${share.truncated ? '<div class="group-limit">仅展示前 1,000 个一级项目</div>' : ''}</div>
    </article>`;
  }).join('');
  document.querySelector('#empty-files').classList.toggle('hidden', groups.length > 0);
}

function renderManage() {
  document.querySelector('#share-count').textContent = `${shares.length} 个文件夹`;
  document.querySelector('#share-list').innerHTML = shares.map(share => `<article class="share-card"><div class="share-card-main"><div class="share-card-name"><span class="folder-icon">▰</span>${escapeHTML(share.name)}</div><div class="share-location">${escapeHTML(share.networkPath)} · ${escapeHTML(share.host)}\\${escapeHTML(share.username)}</div></div><div class="share-actions"><button class="text-button" data-edit="${share.id}" type="button">编辑</button><button class="text-button delete" data-delete="${share.id}" type="button">删除</button></div></article>`).join('');
  document.querySelector('#empty-shares').classList.toggle('hidden', shares.length > 0);
}

function renderAnalysis() {
  const analyzableShares = shares.filter(share => share.available && share.files.length);
  document.querySelector('#analysis-grid').innerHTML = shares.map(share => {
    if (!share.available) return `<article class="analysis-card"><div class="analysis-card-head"><div><h2>${escapeHTML(share.name)}</h2><div class="analysis-path">${escapeHTML(share.networkPath)}</div></div></div><p class="analysis-unavailable">服务器暂时无法读取此共享目录。</p></article>`;
    if (!share.files.length) return `<article class="analysis-card"><div class="analysis-card-head"><div><h2>${escapeHTML(share.name)}</h2><div class="analysis-path">${escapeHTML(share.networkPath)}</div></div></div><p class="analysis-unavailable">此目录中尚未发现可分析的文件。</p></article>`;
    const totals = new Map(fileCategories.map(category => [category.label, 0]));
    share.files.forEach(file => { const category = categoryForFile(file); totals.set(category.label, totals.get(category.label) + 1); });
    const categories = fileCategories.map(category => ({ ...category, count: totals.get(category.label) })).filter(category => category.count);
    let progress = 0;
    const segments = categories.map(category => { const start = progress; progress += category.count / share.files.length * 100; return `${category.color} ${start.toFixed(2)}% ${progress.toFixed(2)}%`; }).join(', ');
    return `<article class="analysis-card"><div class="analysis-card-head"><div><h2>${escapeHTML(share.name)}</h2><div class="analysis-path">${escapeHTML(share.networkPath)}</div></div><span class="analysis-total">${share.files.length} 个一级项目</span></div><div class="analysis-body"><div class="donut" style="background:conic-gradient(${segments})"><div class="donut-center">${share.files.length}<span>一级项目</span></div></div><div class="analysis-legend">${categories.map(category => `<div class="legend-item"><span class="legend-swatch" style="background:${category.color}"></span><span class="legend-label">${category.label}</span><span class="legend-value">${category.count} · ${Math.round(category.count / share.files.length * 100)}%</span></div>`).join('')}</div></div></article>`;
  }).join('');
  document.querySelector('#empty-analysis').classList.toggle('hidden', analyzableShares.length > 0 || shares.length > 0);
}

function render() { renderFiles(); renderManage(); renderAnalysis(); }
function showView(view) { document.querySelector('#files-view').classList.toggle('hidden', view !== 'files'); document.querySelector('#manage-view').classList.toggle('hidden', view !== 'manage'); document.querySelector('#analysis-view').classList.toggle('hidden', view !== 'analysis'); document.querySelectorAll('[data-view-link]').forEach(link => link.classList.toggle('active', link.dataset.viewLink === view)); }
function syncRoute() { showView(location.hash === '#manage' ? 'manage' : location.hash === '#analysis' ? 'analysis' : 'files'); }

const shareDialog = document.querySelector('#share-dialog');
function openShareDialog(id) {
  const share = shares.find(item => item.id === id);
  document.querySelector('#share-form').reset(); document.querySelector('#editing-id').value = share?.id || '';
  document.querySelector('#dialog-kicker').textContent = share ? '编辑位置' : '新增位置'; document.querySelector('#dialog-title').textContent = share ? '编辑共享文件夹' : '添加共享文件夹';
  document.querySelector('#password-hint').textContent = share ? '留空则保留已保存的凭据' : '首次添加时必填';
  if (share) { document.querySelector('#share-host').value = share.host; document.querySelector('#share-name').value = share.shareName; document.querySelector('#share-username').value = share.username; }
  shareDialog.showModal(); document.querySelector('#share-host').focus();
}
function requireToken() { if (adminToken) return true; const entered = prompt('请输入管理员口令以修改共享目录：'); if (!entered) return false; adminToken = entered; sessionStorage.setItem('lan-file-hub-admin-token', entered); return true; }

document.querySelector('#add-share-button').addEventListener('click', () => openShareDialog());
document.querySelector('#close-dialog').addEventListener('click', () => shareDialog.close());
document.querySelector('#cancel-dialog').addEventListener('click', () => shareDialog.close());
document.querySelector('#file-search').addEventListener('input', renderFiles);
document.querySelector('#share-groups').addEventListener('click', event => {
  const button = event.target.closest('[data-toggle-share]');
  if (!button) return;
  const id = button.dataset.toggleShare;
  if (collapsedShares.has(id)) collapsedShares.delete(id); else collapsedShares.add(id);
  renderFiles();
});
document.querySelector('#share-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!requireToken()) return;
  const id = document.querySelector('#editing-id').value;
  const body = JSON.stringify({ host: document.querySelector('#share-host').value.trim(), shareName: document.querySelector('#share-name').value.trim(), username: document.querySelector('#share-username').value.trim(), password: document.querySelector('#share-password').value });
  try { await request(id ? `/api/shares/${encodeURIComponent(id)}` : '/api/shares', { method: id ? 'PUT' : 'POST', body }); shareDialog.close(); await loadShares(); toast(id ? '共享文件夹已更新' : '共享文件夹已添加'); }
  catch (error) { if (error.status === 401) { adminToken = ''; sessionStorage.removeItem('lan-file-hub-admin-token'); } toast(error.message); }
});
document.querySelector('#share-list').addEventListener('click', async event => {
  const button = event.target.closest('button'); if (!button) return; const id = button.dataset.edit || button.dataset.delete;
  if (button.dataset.edit) return openShareDialog(id);
  const share = shares.find(item => item.id === id);
  if (!confirm(`确定从系统中移除“${share.name}”吗？\n真实文件不会被删除。`) || !requireToken()) return;
  try { await request(`/api/shares/${encodeURIComponent(id)}`, { method: 'DELETE' }); await loadShares(); toast('已移除共享文件夹'); }
  catch (error) { if (error.status === 401) { adminToken = ''; sessionStorage.removeItem('lan-file-hub-admin-token'); } toast(error.message); }
});
window.addEventListener('hashchange', syncRoute);
syncRoute(); loadShares();
