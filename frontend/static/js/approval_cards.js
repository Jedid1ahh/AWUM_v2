(function () {
  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  }
  function attr(value) { return esc(value); }
  function badge(priority) {
    const p = String(priority || 'medium').toLowerCase();
    const cls = p === 'critical' || p === 'urgent' ? 'danger' : p === 'high' ? 'warning text-dark' : p === 'opportunity' ? 'info text-dark' : 'secondary';
    return `<span class="badge bg-${cls}">${esc(priority || 'medium')}</span>`;
  }
  async function postDecision(id, decision, options) {
    options = options || {};
    const counter = decision === 'counter'
      ? (options.counterPitch !== undefined ? options.counterPitch : (prompt(options.counterPrompt || 'Counter-pitch:', options.counterDefault || 'Keep the idea, but adjust the execution.') || ''))
      : '';
    const payload = { decision, counter_pitch: counter, notes: options.notes || `Handled from ${options.surface || 'approval card'}: ${decision}` };
    if (window.fetchAPI) {
      return window.fetchAPI(`/api/booker/approval-queue/${encodeURIComponent(id)}/decision`, { method: 'POST', body: JSON.stringify(payload) });
    }
    const response = await fetch(`/api/booker/approval-queue/${encodeURIComponent(id)}/decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || 'Could not resolve approval item');
    return result;
  }
  function renderApprovalCard(item, options) {
    options = options || {};
    const id = attr(item.id);
    const pending = (item.status || 'pending') === 'pending';
    const cardClass = options.cardClass || 'approval-card decision-card';
    const titleClass = options.titleClass || 'approval-title decision-title';
    const metaClass = options.metaClass || 'approval-meta decision-meta';
    const decisionFn = options.decisionFn || 'decideApprovalCardItem';
    return `<div class="${cardClass}" data-approval-id="${id}">
      <div class="d-flex justify-content-between gap-2">
        <div class="${titleClass}">${esc(item.title || 'Approval item')}</div>
        ${badge(item.priority)}
      </div>
      <div class="${metaClass} mb-2">${esc(item.source_type || '')} · ${esc(item.category || '')} · ${esc(item.status || 'pending')} · due Y${esc(item.deadline_year || '-')} W${esc(item.deadline_week || '-')}</div>
      <p class="mb-2">${esc(item.summary || '')}</p>
      ${options.showRecommendation ? `<details class="mb-3"><summary>Recommendation data</summary><pre class="json-preview">${esc(JSON.stringify(item.recommendation_json || {}, null, 2))}</pre></details>` : ''}
      ${pending ? `<div class="btn-group btn-group-sm">
        <button class="btn btn-success" onclick="window.${decisionFn}('${id}','approve')">Approve</button>
        <button class="btn btn-warning" onclick="window.${decisionFn}('${id}','counter')">Counter</button>
        <button class="btn btn-danger" onclick="window.${decisionFn}('${id}','reject')">Reject</button>
        <button class="btn btn-outline-secondary" onclick="window.${decisionFn}('${id}','dismiss')">Dismiss</button>
      </div>` : `<span class="badge bg-secondary">Resolved: ${esc(item.status || 'resolved')}</span>`}
    </div>`;
  }
  window.renderApprovalCard = renderApprovalCard;
  window.decideApprovalQueueItem = postDecision;
})();
