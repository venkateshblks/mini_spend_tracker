const form = document.querySelector('#expense-form');
const filterForm = document.querySelector('#filter-form');
const month = document.querySelector('#month');
const today = new Date();
const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
document.querySelector('#expense-date').value = localDate;
month.value = localDate.slice(0, 7);
let latestSummaryRefresh = 0;
let latestExpenseRefresh = 0;
let appliedFilters = new URLSearchParams();
const pageSize = 20;
let currentOffset = 0;
let hasMore = false;
const previousPage = document.querySelector('#previous-page');
const nextPage = document.querySelector('#next-page');
const knownCategories = new Set(Array.from(document.querySelector('#categories').options, option => option.value));

function rememberCategories(names) {
  for (const name of names) knownCategories.add(name);
  const options = Array.from(knownCategories).sort().map(name => {
    const option = document.createElement('option');
    option.value = name;
    return option;
  });
  document.querySelector('#categories').replaceChildren(...options);
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error?.message || 'Request failed. Please try again.');
  return data;
}

async function refreshSummary() {
  const version = ++latestSummaryRefresh;
  const status = document.querySelector('#summary-status');
  const content = document.querySelector('#summary-content');
  content.hidden = true;
  if (!month.value) { status.textContent = 'Choose a month to view your summary.'; return; }
  status.textContent = 'Loading…';
  try {
    const summary = await api(`/summary?month=${encodeURIComponent(month.value)}`);
    if (version !== latestSummaryRefresh) return;
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
    rememberCategories(Object.keys(summary.spend_by_category));
    status.textContent = '';
    content.hidden = false;
  } catch (error) {
    if (version === latestSummaryRefresh) status.textContent = `Could not load summary: ${error.message}`;
  }
}

async function refreshExpenses(offset = 0) {
  const version = ++latestExpenseRefresh;
  const status = document.querySelector('#expenses-status');
  const table = document.querySelector('#expense-table');
  const empty = document.querySelector('#empty');
  table.hidden = true;
  empty.hidden = true;
  status.textContent = 'Loading expenses…';
  previousPage.disabled = true;
  nextPage.disabled = true;
  try {
    const query = new URLSearchParams(appliedFilters);
    query.set('limit', pageSize);
    query.set('offset', offset);
    const page = await api(`/expenses?${query.toString()}`);
    if (version !== latestExpenseRefresh) return;
    const expenses = page.expenses;
    currentOffset = page.offset;
    hasMore = page.has_more;
    document.querySelector('#page-label').textContent = `Page ${Math.floor(currentOffset / pageSize) + 1}`;
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
    rememberCategories(expenses.map(expense => expense.category));
    empty.hidden = expenses.length !== 0;
    empty.textContent = currentOffset > 0
      ? 'No expenses on this page. Go back or apply your filters again.'
      : appliedFilters.size ? 'No expenses match these filters. Try another category or date range.'
      : 'No expenses yet. Add your first one.';
    table.hidden = expenses.length === 0;
    status.textContent = expenses.length ? `Showing ${currentOffset + 1}–${currentOffset + expenses.length}${appliedFilters.size ? ' of the matching expenses' : ' expenses'}.` : '0 expenses on this page.';
  } catch (error) {
    if (version === latestExpenseRefresh) status.textContent = `Could not load expenses: ${error.message}`;
  } finally {
    if (version === latestExpenseRefresh) {
      previousPage.disabled = currentOffset === 0;
      nextPage.disabled = !hasMore;
    }
  }
}

previousPage.addEventListener('click', () => refreshExpenses(Math.max(0, currentOffset - pageSize)));
nextPage.addEventListener('click', () => refreshExpenses(currentOffset + pageSize));

filterForm.addEventListener('submit', (event) => {
  event.preventDefault();
  const filters = new URLSearchParams();
  for (const [name, value] of new FormData(filterForm)) {
    if (value.trim()) filters.set(name, value.trim());
  }
  if (filters.has('start_date') && filters.has('end_date') && filters.get('start_date') > filters.get('end_date')) {
    ++latestExpenseRefresh; // An older response must not replace this validation error.
    previousPage.disabled = true;
    nextPage.disabled = true;
    document.querySelector('#expense-table').hidden = true;
    document.querySelector('#empty').hidden = true;
    document.querySelector('#expenses-status').textContent = 'From date must be on or before To date. Update the dates and apply again.';
    return;
  }
  appliedFilters = filters;
  currentOffset = 0;
  hasMore = false;
  refreshExpenses();
});

document.querySelector('#clear-filters').addEventListener('click', () => {
  filterForm.reset();
  appliedFilters = new URLSearchParams();
  currentOffset = 0;
  hasMore = false;
  refreshExpenses();
});

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
    status.textContent = appliedFilters.size ? 'Expense saved. Active filters may hide it from the list.' : 'Expense saved.';
    currentOffset = 0;
    hasMore = false;
    await Promise.all([refreshSummary(), refreshExpenses()]);
  } catch (error) { status.textContent = `Could not save expense: ${error.message}`; }
  finally { button.disabled = false; }
});
month.addEventListener('change', refreshSummary);
refreshSummary();
refreshExpenses();
