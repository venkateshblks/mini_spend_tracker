const form = document.querySelector('#expense-form');
const month = document.querySelector('#month');
const today = new Date();
const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
document.querySelector('#expense-date').value = localDate;
month.value = localDate.slice(0, 7);
let latestRefresh = 0;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || 'Request failed. Please try again.');
  return data;
}

async function refresh() {
  const version = ++latestRefresh;
  const status = document.querySelector('#summary-status');
  const content = document.querySelector('#summary-content');
  content.hidden = true;
  if (!month.value) { status.textContent = 'Choose a month to view your summary.'; return; }
  status.textContent = 'Loading…';
  try {
    const selected = month.value;
    const [year, number] = selected.split('-').map(Number);
    const days = number === 2 ? ((year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)) ? 29 : 28) : ([4, 6, 9, 11].includes(number) ? 30 : 31);
    const [summary, expenses] = await Promise.all([
      api(`/summary?month=${encodeURIComponent(selected)}`),
      api(`/expenses?start_date=${selected}-01&end_date=${selected}-${days}`),
    ]);
    if (version !== latestRefresh) return;
    document.querySelector('#total').textContent = summary.total_spend;
    const percentage = summary.month_over_month_change_percent;
    document.querySelector('#comparison').textContent = percentage === null
      ? `Previous month (${summary.previous_month}): ${summary.previous_month_total}. Percentage change unavailable because previous spend was zero.`
      : `${percentage > 0 ? '+' : ''}${percentage}% vs ${summary.previous_month} (${summary.previous_month_total}). Change: ${summary.month_over_month_change}.`;
    const categories = document.querySelector('#categories-summary');
    categories.replaceChildren();
    for (const [name, amount] of Object.entries(summary.spend_by_category)) {
      const li = document.createElement('li');
      li.textContent = `${name}: ${amount}`;
      categories.append(li);
    }
    const insights = document.querySelector('#insights');
    insights.replaceChildren();
    for (const insight of summary.insights) {
      const p = document.createElement('p');
      p.className = 'insight';
      p.textContent = `${insight.category} spending increased ${insight.change_percent}% from last month.`;
      insights.append(p);
    }
    const rows = document.querySelector('#expense-rows');
    rows.replaceChildren();
    for (const expense of expenses) {
      const tr = document.createElement('tr');
      for (const value of [expense.date, expense.category + (expense.note ? ` — ${expense.note}` : ''), expense.amount]) {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      }
      rows.append(tr);
    }
    document.querySelector('#empty').hidden = expenses.length !== 0;
    document.querySelector('#expense-table').hidden = expenses.length === 0;
    status.textContent = '';
    content.hidden = false;
  } catch (error) {
    if (version === latestRefresh) status.textContent = `Could not load summary: ${error.message}`;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = document.querySelector('#save-button');
  const status = document.querySelector('#save-status');
  button.disabled = true;
  status.textContent = 'Saving…';
  try {
    const expense = await api('/expenses', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    form.reset();
    document.querySelector('#expense-date').value = localDate;
    month.value = expense.date.slice(0, 7);
    status.textContent = 'Expense saved.';
    await refresh();
  } catch (error) { status.textContent = `Could not save expense: ${error.message}`; }
  finally { button.disabled = false; }
});
month.addEventListener('change', refresh);
refresh();
