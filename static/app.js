const $ = (id) => document.getElementById(id);
let config, ledger, requestSequence = 0;
const money = (amount, currency) => new Intl.NumberFormat('en-IN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(amount));
const niceDate = (value) => new Date(`${value}T12:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your invoice fields and try again.');
  return data;
}

function renderInvoices(rows) {
  $('results').replaceChildren();
  for (const row of rows) {
    const invoice = row.invoice || row;
    const card = $('invoice-template').content.cloneNode(true);
    const set = (selector, text) => { card.querySelector(selector).textContent = text; };
    const overdue = invoice.status === 'unpaid' && invoice.due_date < $('as-of').value;
    set('.invoice-id', invoice.id);
    set('.status-pill', overdue ? 'Overdue' : invoice.status[0].toUpperCase() + invoice.status.slice(1));
    card.querySelector('.status-pill').classList.add(overdue ? 'overdue' : invoice.status);
    set('.client', invoice.client); set('.description', invoice.description);
    for (const value of invoice.tags) {
      const tag = document.createElement('span'); tag.textContent = value; card.querySelector('.tags').append(tag);
    }
    set('.amount', money(invoice.amount, invoice.currency));
    set('.due', `Due ${niceDate(invoice.due_date)}`);
    set('.similarity', row.similarity === undefined ? '' : `${row.similarity.toFixed(2)} similarity`);
    const followUp = card.querySelector('.follow-up');
    if (invoice.status !== 'unpaid') followUp.remove();
    else followUp.addEventListener('click', async () => {
      const draft = `Hi ${invoice.client},\n\nA quick follow-up on invoice ${invoice.id} for ${money(invoice.amount, invoice.currency)}, due ${niceDate(invoice.due_date)}. The work covered: ${invoice.description}\n\nCould you let me know the expected payment date? Thank you!`;
      try { await navigator.clipboard.writeText(draft); followUp.textContent = 'Draft copied — review before sending ✓'; }
      catch { $('feedback').textContent = 'Clipboard is unavailable in this browser. No message was sent.'; }
    });
    $('results').append(card);
  }
  if (!rows.length) {
    const empty = document.createElement('div'); empty.className = 'empty-state';
    const heading = document.createElement('h3'); heading.textContent = 'No strong matches this time.';
    const message = document.createElement('p'); message.textContent = 'Try a shorter description or remove a filter. The model can miss a connection; your complete invoice shelf is always available.';
    empty.append(heading, message); $('results').append(empty);
  }
}

function showMetrics() {
  $('metric-count').textContent = ledger.invoices.length;
  $('metric-unpaid').textContent = money(ledger.totals.INR?.unpaid || '0', 'INR');
  $('metric-overdue').textContent = money(ledger.totals.INR?.overdue || '0', 'INR');
  const others = Object.entries(ledger.totals).filter(([currency]) => currency !== 'INR').map(([currency, value]) => `${money(value.unpaid, currency)} ${currency} also outstanding`);
  $('metric-usd').textContent = others.join(' · ') || 'Currencies are kept separate';
  $('metric-date').textContent = `As of ${niceDate($('as-of').value)}`;
}

async function loadLedger() {
  ledger = await api(`/api/invoices?as_of=${encodeURIComponent($('as-of').value)}`);
  showMetrics();
}

function allRecords() {
  ++requestSequence; renderInvoices(ledger.invoices);
  $('results-heading').textContent = 'Your invoice shelf';
  $('results-caption').textContent = `${ledger.invoices.length} records · exact amounts, original descriptions`;
  $('feedback').textContent = '';
}

async function search(event) {
  event?.preventDefault();
  const query = $('query').value.trim(); if (!query) { allRecords(); return; }
  const requestId = ++requestSequence;
  $('search-button').disabled = true; $('feedback').textContent = 'Matching the meaning of your words…';
  try {
    const result = await api('/api/search', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, status: $('status').value, currency: $('currency').value, due_before: $('due-before').value || null, overdue_only: $('overdue-only').checked, as_of: $('as-of').value }) });
    if (requestId !== requestSequence) return;
    renderInvoices(result.results); $('results-heading').textContent = 'Found in your records';
    $('results-caption').textContent = `${result.matched_count} matching invoice${result.matched_count === 1 ? '' : 's'} for “${query}”`;
    $('feedback').textContent = result.notice;
  } catch (error) { if (requestId === requestSequence) $('feedback').textContent = error.message; }
  finally { $('search-button').disabled = false; }
}

$('search-form').addEventListener('submit', search);
document.querySelectorAll('[data-query]').forEach(button => button.addEventListener('click', () => {
  $('query').value = button.dataset.query; $('status').value = button.dataset.status;
  $('currency').value = 'all'; $('due-before').value = ''; $('overdue-only').checked = false; search();
}));
$('show-all').addEventListener('click', allRecords);
$('as-of').addEventListener('change', async () => {
  if (!$('as-of').value) return;
  try { await loadLedger(); $('query').value.trim() ? await search() : allRecords(); }
  catch (error) { $('feedback').textContent = error.message; }
});
$('csv-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 1_000_000) { $('feedback').textContent = 'Please choose a CSV file under 1 MB.'; return; }
  const form = new FormData(); form.append('file', file); $('feedback').textContent = 'Checking and indexing your invoices…';
  try { const result = await api('/api/import', { method: 'POST', body: form }); await loadLedger(); allRecords(); $('feedback').textContent = `${result.imported} invoices imported. Your records are saved on this app’s host.`; }
  catch (error) { $('feedback').textContent = error.message; }
  finally { event.target.value = ''; }
});

(async () => {
  try {
    config = await api('/api/config'); $('as-of').value = config.as_of;
    $('workspace-notice').textContent = config.demo ? 'PUBLIC DEMO · All clients and invoices below are fictional. Run locally to import private records.' : 'LOCAL WORKSPACE · Imports are saved on this app’s host. Keep the server on your own computer for private records.';
    if (config.demo) { $('csv-file').disabled = true; $('import-label').classList.add('disabled'); }
    $('import-note').textContent = config.demo ? 'Import is disabled in the shared demo. The source includes a fully working local CSV import.' : 'UTF-8 CSV · 1 MB maximum · unique invoice IDs · duplicate imports are rejected as a whole.';
    await loadLedger(); allRecords();
  } catch (error) { $('workspace-notice').textContent = 'The workspace could not load. Please refresh in a moment.'; $('feedback').textContent = error.message; }
})();
