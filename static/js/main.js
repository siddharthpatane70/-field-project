/**
 * TVS Parts Sales Prediction, Inventory Management & Workshop Hub
 * Core Frontend JavaScript Engine
 */

document.addEventListener("DOMContentLoaded", () => {
    const path = window.location.pathname;

    if (path === "/" || path.includes("index")) {
        loadInteractiveDashboard();
    } else if (path.includes("inventory")) {
        loadInventoryTable();
    } else if (path.includes("analytics")) {
        loadAnalyticsEngine();
    } else if (path.includes("billing")) {
        loadBillingProductsDropdown();
        loadSavedInvoicesHistory();
    } else if (path.includes("service-hub")) {
        loadJobCardsData();
        loadJobCardPartsDropdown();
    } else if (path.includes("customer-ledger")) {
        loadCustomerLedgerData();
    } else if (path.includes("forecast-hub")) {
        loadForecastSKUDropdown();
    }
});

/* ==========================================================================
   1. GLOBAL STATE & PRESETS
   ========================================================================== */
const TVS_PART_PRESETS = {
    apache_brake_pad: { name: "TVS Apache RTR 200 Front Brake Pad", category: "Brakes", price: 850.00, stock: 45, reorder: 15 },
    jupiter_drive_belt: { name: "TVS Jupiter Drive Belt", category: "Drive Train", price: 620.00, stock: 30, reorder: 10 },
    ntorq_air_filter: { name: "TVS NTORQ 125 Air Filter", category: "Engine", price: 310.00, stock: 8, reorder: 12 },
    xl100_spark_plug: { name: "TVS XL100 Spark Plug", category: "Electrical", price: 180.00, stock: 120, reorder: 25 },
    rider_chain_sprocket: { name: "TVS Raider 125 Chain Sprocket Kit", category: "Transmission", price: 1450.00, stock: 18, reorder: 5 }
};

let activePartIdToDelete = null;
let barChartInstance = null;
let pieChartInstance = null;
let lineChartInstance = null;
let forecastChartInstance = null;
let currentForecastSKU = null;
let currentInvoiceItems = [];
let globalLedgerRecords = [];
let inventoryCache = [];
let analyticsProducts = [];
let analyticsRequestNumber = 0;
let analyticsActiveProduct = null;

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[character]);
}

/* ==========================================================================
   2. DASHBOARD OVERVIEW ENGINE
   ========================================================================== */

async function loadDashboardSummary() {
    try {
        const [response, metricsResponse] = await Promise.all([
            fetch('/api/inventory'),
            fetch('/api/dashboard/summary')
        ]);
        if (!response.ok || !metricsResponse.ok) throw new Error('Dashboard data could not be loaded.');
        const items = await response.json();
        const metrics = await metricsResponse.json();

        const totalPartsEl = document.getElementById("kpi-total-parts");
        if (totalPartsEl) totalPartsEl.innerText = metrics.inventory_items;

        const lowStockCount = items.filter(i => i.current_stock <= i.reorder_level).length;
        const lowStockEl = document.getElementById("kpi-low-stock");
        if (lowStockEl) lowStockEl.innerText = metrics.low_stock_items ?? lowStockCount;
        const totalSales = document.getElementById('kpi-total-sales-units');
        if (totalSales) totalSales.innerText = Number(metrics.recorded_sales_units || 0).toLocaleString();

        const tableBody = document.getElementById("summary-table-body");
        if (tableBody) {
            tableBody.innerHTML = "";
            items.slice(0, 5).forEach(item => {
                const isLow = item.current_stock <= item.reorder_level;
                const status = isLow ? `<span class="badge bg-warning text-dark">Low Stock</span>` : `<span class="badge bg-success">Healthy</span>`;
                const row = document.createElement("tr");
                row.innerHTML = `
                    <td class="fw-bold">${escapeHtml(item.part_name)}</td>
                    <td>${escapeHtml(item.category)}</td>
                    <td>₹${parseFloat(item.unit_price).toFixed(2)}</td>
                    <td>${item.current_stock}</td>
                    <td>${status}</td>
                `;
                tableBody.appendChild(row);
            });
        }
        const modelStatus = document.getElementById('kpi-model-status');
        if (modelStatus) modelStatus.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles me-1"></i> Local AI forecast';
    } catch (err) {
        console.error("Failed to load dashboard metrics:", err);
    }
}

/* ==========================================================================
   3. INVENTORY CRUD MANAGEMENT
   ========================================================================== */

async function loadInventoryTable() {
    const tableBody = document.getElementById("inventory-table-body");
    if (!tableBody) return;

    try {
        const response = await fetch('/api/inventory');
        if (!response.ok) throw new Error('Inventory could not be loaded.');
        inventoryCache = await response.json();
        const categoryFilter = document.getElementById('inventory-category-filter');
        if (categoryFilter) {
            const selected = categoryFilter.value;
            const categories = [...new Set(inventoryCache.map((item) => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b));
            categoryFilter.replaceChildren(new Option('All categories', ''), ...categories.map((value) => new Option(value, value)));
            categoryFilter.value = categories.includes(selected) ? selected : '';
        }
        renderInventoryTable();
    } catch (err) {
        console.error("Failed to load inventory:", err);
    }
}

function renderInventoryTable() {
    const tableBody = document.getElementById("inventory-table-body");
    if (!tableBody) return;
    const query = (document.getElementById('inventory-search')?.value || '').trim().toLocaleLowerCase();
    const category = document.getElementById('inventory-category-filter')?.value || '';
    const lowStockOnly = document.getElementById('inventory-stock-filter')?.value === '1';
    const items = inventoryCache.filter((item) => {
        const text = `${item.part_name} ${item.category}`.toLocaleLowerCase();
        return text.includes(query) && (!category || item.category === category) && (!lowStockOnly || item.current_stock <= item.reorder_level);
    });

    tableBody.replaceChildren();
    const lowCount = items.filter((item) => item.current_stock <= item.reorder_level).length;
    const value = items.reduce((sum, item) => sum + Math.max(0, Number(item.current_stock)) * Number(item.unit_price), 0);
    document.getElementById('inventory-stat-count').textContent = items.length.toLocaleString();
    document.getElementById('inventory-stat-low').textContent = lowCount.toLocaleString();
    document.getElementById('inventory-stat-value').textContent = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value);

        if (items.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">No products match these filters.</td></tr>`;
            return;
        }

        items.forEach(item => {
            const isLowStock = item.current_stock <= item.reorder_level;
            const statusBadge = isLowStock
                ? `<span class="badge-stock-low"><i class="fa-solid fa-triangle-exclamation me-1"></i> Low Stock</span>`
                : `<span class="badge-stock-healthy"><i class="fa-solid fa-check me-1"></i> In Stock</span>`;

            const row = document.createElement("tr");
            row.innerHTML = `
                <td><strong>#${item.id}</strong></td>
                <td class="fw-bold text-dark">${escapeHtml(item.part_name)}</td>
                <td><span class="badge bg-light text-dark border">${escapeHtml(item.category)}</span></td>
                <td class="fw-bold">₹${parseFloat(item.unit_price).toFixed(2)}</td>
                <td><span class="fs-6 fw-bold">${item.current_stock}</span></td>
                <td class="text-muted">${item.reorder_level}</td>
                <td>${statusBadge}</td>
                <td class="text-center">
                    <div class="btn-group btn-group-sm" role="group">
                        <button class="btn btn-outline-success fw-bold" onclick="quickDeductSale(${item.id}, 1)" title="Log 1 Sold">
                            +1 Sold
                        </button>
                        <button class="btn btn-outline-secondary" onclick="quickDeductSale(${item.id}, 5)" title="Log 5 Sold">
                            +5
                        </button>
                    </div>
                </td>
                <td class="text-end me-3">
                    <button class="btn btn-sm btn-outline-success me-1" onclick="openReceiveStockModal(${item.id})" title="Receive stock"><i class="fa-solid fa-box-open"></i></button>
                    <button class="btn btn-sm btn-outline-secondary me-1" onclick="viewStockHistory(${item.id})" title="View stock movements"><i class="fa-solid fa-clock-rotate-left"></i></button>
                    <button class="btn btn-sm btn-outline-primary me-1" onclick="openEditPartModal(${JSON.stringify(item).replace(/"/g, '&quot;')})">
                        <i class="fa-solid fa-pen"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="promptDeletePart(${item.id}, ${JSON.stringify(item.part_name).replace(/"/g, '&quot;')})">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            `;
            tableBody.appendChild(row);
        });
}

async function quickDeductSale(partId, quantity) {
    const today = new Date();
    const salesPayload = {
        part_id: partId,
        month: today.getMonth() + 1,
        year: today.getFullYear(),
        units_sold: quantity
    };

    try {
        const response = await fetch('/api/sales', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(salesPayload)
        });

        if (response.ok) {
            loadInventoryTable();
            if (document.getElementById("monthlySalesBarChart")) {
                refreshAnalyticsCharts();
            }
        }
    } catch (err) {
        console.error("Failed to log quick sale:", err);
    }
}

function autoFillPartForm(presetKey) {
    const preset = TVS_PART_PRESETS[presetKey];
    if (!preset) return;

    openAddPartModal();
    document.getElementById("part_name").value = preset.name;
    document.getElementById("category").value = preset.category;
    document.getElementById("unit_price").value = preset.price;
    document.getElementById("current_stock").value = preset.stock;
    document.getElementById("reorder_level").value = preset.reorder;
}

function openAddPartModal() {
    const form = document.getElementById("partForm");
    if (form) form.reset();
    document.getElementById("part_id").value = "";
    document.getElementById("partModalLabel").innerText = "Add New TVS Product";
    document.getElementById("savePartBtn").innerText = "Save Product";

    const modal = new bootstrap.Modal(document.getElementById('partModal'));
    modal.show();
}

function openEditPartModal(item) {
    document.getElementById("part_id").value = item.id;
    document.getElementById("part_name").value = item.part_name;
    document.getElementById("category").value = item.category;
    document.getElementById("unit_price").value = item.unit_price;
    document.getElementById("current_stock").value = item.current_stock;
    document.getElementById("reorder_level").value = item.reorder_level;

    document.getElementById("partModalLabel").innerText = "Edit TVS Product";
    document.getElementById("savePartBtn").innerText = "Update Product";

    const modal = new bootstrap.Modal(document.getElementById('partModal'));
    modal.show();
}

async function handleSavePart(event) {
    event.preventDefault();

    const partId = document.getElementById("part_id").value;
    const payload = {
        part_name: document.getElementById("part_name").value,
        category: document.getElementById("category").value,
        unit_price: parseFloat(document.getElementById("unit_price").value),
        current_stock: parseInt(document.getElementById("current_stock").value),
        reorder_level: parseInt(document.getElementById("reorder_level").value)
    };

    const url = partId ? `/api/inventory/${partId}` : '/api/inventory';
    const method = partId ? 'PUT' : 'POST';

    try {
        const response = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not save this product.');
        {
            const modalEl = document.getElementById('partModal');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
            loadInventoryTable();
            populateProductFilterDropdown();
            if (document.getElementById('summary-table-body')) loadDashboardSummary();
        }
    } catch (err) {
        alert(err.message || "Save operation failed.");
    }
}

function promptDeletePart(id, name) {
    activePartIdToDelete = id;
    document.getElementById("delete-product-name").innerText = name;

    const modal = new bootstrap.Modal(document.getElementById('deleteConfirmModal'));
    modal.show();

    document.getElementById("confirmDeleteBtn").onclick = async () => {
        if (!activePartIdToDelete) return;
        try {
            const response = await fetch(`/api/inventory/${activePartIdToDelete}`, { method: 'DELETE' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Could not delete this product.');
            modal.hide();
            loadInventoryTable();
            populateProductFilterDropdown();
            if (document.getElementById('summary-table-body')) loadDashboardSummary();
        } catch (err) {
            alert(err.message || "Delete operation failed.");
        }
    };
}

function filterInventoryTable() {
    renderInventoryTable();
}

function openReceiveStockModal(partId) {
    const item = inventoryCache.find((row) => Number(row.id) === Number(partId));
    if (!item) return;
    document.getElementById('receive-part-id').value = item.id;
    document.getElementById('receive-part-name').textContent = `${item.part_name} · on hand ${item.current_stock}`;
    document.getElementById('receiveStockForm').reset();
    document.getElementById('receive-part-id').value = item.id;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('receiveStockModal')).show();
}

async function viewStockHistory(partId) {
    const item = inventoryCache.find((row) => Number(row.id) === Number(partId));
    const title = document.getElementById('stock-history-title');
    const body = document.getElementById('stock-history-body');
    title.textContent = `${item?.part_name || 'Product'} · movements`;
    body.innerHTML = '<tr><td colspan="4" class="text-muted text-center py-4">Loading history…</td></tr>';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('stockHistoryModal')).show();
    try {
        const response = await fetch(`/api/inventory/${partId}/movements?limit=200`);
        const rows = await response.json();
        if (!response.ok) throw new Error(rows.error || 'Could not load stock history.');
        body.replaceChildren();
        if (!rows.length) {
            body.innerHTML = '<tr><td colspan="4" class="text-muted text-center py-4">No movements have been recorded yet.</td></tr>';
            return;
        }
        rows.forEach((movement) => {
            const row = document.createElement('tr');
            const date = document.createElement('td');
            date.textContent = movement.created_at?.replace('T', ' ') || '—';
            const type = document.createElement('td');
            type.textContent = String(movement.movement_type || '').replaceAll('_', ' ');
            const delta = document.createElement('td');
            delta.className = Number(movement.quantity_delta) >= 0 ? 'text-success fw-semibold' : 'text-danger fw-semibold';
            delta.textContent = `${Number(movement.quantity_delta) > 0 ? '+' : ''}${movement.quantity_delta}`;
            const note = document.createElement('td');
            note.textContent = [movement.reference, movement.notes].filter(Boolean).join(' · ') || '—';
            row.append(date, type, delta, note);
            body.appendChild(row);
        });
    } catch (error) {
        body.innerHTML = `<tr><td colspan="4" class="text-danger text-center py-4">${escapeHtml(error.message)}</td></tr>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('inventory-category-filter')?.addEventListener('change', renderInventoryTable);
    document.getElementById('inventory-stock-filter')?.addEventListener('change', renderInventoryTable);
    document.getElementById('receiveStockForm')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const partId = document.getElementById('receive-part-id').value;
        const response = await fetch(`/api/inventory/${partId}/receive`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                quantity: Number(document.getElementById('receive-quantity').value),
                reference: document.getElementById('receive-reference').value,
                notes: document.getElementById('receive-notes').value
            })
        });
        const result = await response.json();
        if (!response.ok) { alert(result.error || 'Could not record the stock receipt.'); return; }
        bootstrap.Modal.getInstance(document.getElementById('receiveStockModal'))?.hide();
        await loadInventoryTable();
    });
});

/* ==========================================================================
   4. DYNAMIC SALES ANALYTICS & MULTI-CHART ENGINE (LIVE DATA)
   ========================================================================== */

async function loadAnalyticsEngine() {
    populateAnalyticsYears();
    await populateProductFilterDropdown();
    await refreshAnalyticsCharts();
}

function populateAnalyticsYears() {
    const select = document.getElementById('analytics-year-filter');
    if (!select) return;
    const currentYear = new Date().getFullYear();
    select.replaceChildren(...Array.from({ length: 6 }, (_, index) => {
        const year = currentYear - index;
        return new Option(year === currentYear ? `Current Year (${year})` : String(year), String(year), index === 0, index === 0);
    }));
}

async function populateProductFilterDropdown() {
    const productFilter = document.getElementById("analytics-product-filter");
    const salesPartSelect = document.getElementById("sales_part_id");
    const predictPartSelect = document.getElementById("predict_part_id");

    try {
        const response = await fetch('/api/inventory');
        if (!response.ok) throw new Error('Could not load product filters.');
        const items = await response.json();
        analyticsProducts = items;

        if (productFilter) {
            const currentSelected = productFilter.value;
            productFilter.innerHTML = '<option value="ALL">-- Compare All Products --</option>';
            items.forEach(item => {
                const opt = document.createElement("option");
                opt.value = item.id;
                opt.textContent = `${item.part_name} (${item.category})`;
                productFilter.appendChild(opt);
            });
            productFilter.value = currentSelected || "ALL";
        }

        if (salesPartSelect) {
            salesPartSelect.innerHTML = '<option value="" disabled selected>Select Target Product...</option>';
            items.forEach(item => {
                const opt = document.createElement("option");
                opt.value = item.id;
                opt.textContent = `${item.part_name} - ₹${parseFloat(item.unit_price).toFixed(2)}`;
                salesPartSelect.appendChild(opt);
            });
        }

        if (predictPartSelect) {
            predictPartSelect.innerHTML = '';
            items.forEach(item => {
                const opt = document.createElement("option");
                opt.value = item.id;
                opt.textContent = item.part_name;
                predictPartSelect.appendChild(opt);
            });
        }
    } catch (err) {
        console.error("Failed to populate dropdowns:", err);
    }
}

async function refreshAnalyticsCharts() {
    const yearFilter = document.getElementById("analytics-year-filter")?.value || new Date().getFullYear();
    const partFilter = document.getElementById("analytics-product-filter")?.value || 'ALL';
    const metricType = document.getElementById("analytics-metric-type")?.value || 'units';

    const requestNumber = ++analyticsRequestNumber;
    try {
        const response = await fetch(`/api/analytics/sales-summary?year=${yearFilter}&part_id=${partFilter}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Sales analytics could not be loaded.');
        if (requestNumber !== analyticsRequestNumber) return;

        renderBarChart(data.monthly_data, metricType);
        renderPieChart(data.product_share, metricType);
        renderLineTrendChart(data.monthly_data, metricType);
        const selectedProduct = analyticsProducts.find((item) => String(item.id) === String(partFilter));
        analyticsActiveProduct = selectedProduct || null;
        document.getElementById('analytics-filter-caption').textContent = selectedProduct
            ? `Focused on ${selectedProduct.part_name}. Click its pie slice to clear the focus.`
            : 'Click a pie slice to focus the dashboard on that product.';
        document.getElementById('analytics-reset-product').hidden = !selectedProduct;
    } catch (err) {
        console.error("Failed to update analytics charts:", err);
    }
}

function renderBarChart(monthlyData, metricType) {
    const ctxBar = document.getElementById("monthlySalesBarChart")?.getContext("2d");
    if (!ctxBar) return;

    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const values = new Array(12).fill(0);

    monthlyData.forEach(item => {
        const monthIdx = item.month - 1;
        if (monthIdx >= 0 && monthIdx < 12) {
            values[monthIdx] = metricType === 'revenue' ? item.total_revenue : item.total_units;
        }
    });

    if (barChartInstance) barChartInstance.destroy();

    barChartInstance = new Chart(ctxBar, {
        type: 'bar',
        data: {
            labels: months,
            datasets: [{
                label: metricType === 'revenue' ? 'Total Revenue (₹)' : 'Units Sold',
                data: values,
                backgroundColor: '#e31e24',
                borderColor: '#b81419',
                borderWidth: 1,
                borderRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: 'top', labels: { color: '#334155' } },
                tooltip: { callbacks: { label: (context) => metricType === 'revenue'
                    ? `${context.dataset.label}: ${formatDashboardMoney(context.raw)}`
                    : `${context.dataset.label}: ${formatDashboardNumber(context.raw)} units` } }
            },
            scales: {
                x: { ticks: { color: '#475569' }, grid: { color: 'rgba(15, 23, 42, 0.05)' } },
                y: { ticks: { color: '#475569' }, grid: { color: 'rgba(15, 23, 42, 0.08)' } }
            }
        }
    });
}

function renderPieChart(productShareData, metricType) {
    const ctxPie = document.getElementById("productSharePieChart")?.getContext("2d");
    if (!ctxPie) return;

    const labels = productShareData.map(s => s.part_name);
    const values = productShareData.map(s => metricType === 'revenue' ? s.total_revenue : s.total_units);
    const total = values.reduce((sum, value) => sum + Number(value || 0), 0);
    const detail = document.getElementById('pie-selection-detail');
    if (detail) detail.textContent = `${metricType === 'revenue' ? 'Recorded value' : 'Recorded units'} · ${metricType === 'revenue' ? formatDashboardMoney(total) : `${formatDashboardNumber(total)} units`}`;

    if (pieChartInstance) pieChartInstance.destroy();

    pieChartInstance = new Chart(ctxPie, {
        type: 'doughnut',
        data: {
            labels: labels.length ? labels : ['No Sales Recorded'],
            datasets: [{
                data: values.length ? values : [0],
                backgroundColor: ['#e31e24', '#0d47a1', '#ff9800', '#4caf50', '#9c27b0', '#00bcd4', '#e91e63']
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '64%',
            onClick: (_event, elements) => {
                if (!elements.length || !productShareData.length) return;
                const selected = productShareData[elements[0].index];
                const productFilter = document.getElementById('analytics-product-filter');
                if (!productFilter) return;
                productFilter.value = String(productFilter.value) === String(selected.part_id) ? 'ALL' : String(selected.part_id);
                refreshAnalyticsCharts();
            },
            plugins: {
                legend: { position: 'bottom', labels: { color: '#334155', font: { size: 10 }, padding: 12 } },
                tooltip: { callbacks: { label: (context) => {
                    const value = Number(context.raw || 0);
                    const share = total ? (100 * value / total).toFixed(1) : '0.0';
                    return `${context.label}: ${metricType === 'revenue' ? formatDashboardMoney(value) : `${formatDashboardNumber(value)} units`} (${share}%)`;
                } } }
            }
        },
        plugins: [{
            id: 'pieCenterValue',
            afterDraw(chart) {
                const { ctx, chartArea } = chart;
                if (!chartArea) return;
                const x = (chartArea.left + chartArea.right) / 2;
                const y = (chartArea.top + chartArea.bottom) / 2;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.fillStyle = '#64748b';
                ctx.font = '600 11px system-ui';
                ctx.fillText(metricType === 'revenue' ? 'VALUE' : 'UNITS', x, y - 7);
                ctx.fillStyle = '#0f172a';
                ctx.font = '700 15px system-ui';
                ctx.fillText(metricType === 'revenue' ? formatDashboardMoney(total) : formatDashboardNumber(total), x, y + 14);
                ctx.restore();
            }
        }]
    });
}

function renderLineTrendChart(monthlyData, metricType) {
    const ctxLine = document.getElementById("salesTrendLineChart")?.getContext("2d");
    if (!ctxLine) return;

    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const actualValues = new Array(12).fill(0);

    monthlyData.forEach(item => {
        const idx = item.month - 1;
        if (idx >= 0 && idx < 12) {
            actualValues[idx] = metricType === 'revenue' ? item.total_revenue : item.total_units;
        }
    });

    const windowSize = Number(document.getElementById('analytics-trend-window')?.value || 12);
    const firstMonth = Math.max(0, 12 - windowSize);
    const visibleLabels = months.slice(firstMonth);
    const visibleValues = actualValues.slice(firstMonth);
    const lineDetail = document.getElementById('line-point-detail');
    if (lineDetail) lineDetail.textContent = `Showing the last ${visibleLabels.length} month(s) · select a point to inspect its total.`;

    if (lineChartInstance) lineChartInstance.destroy();

    lineChartInstance = new Chart(ctxLine, {
        type: 'line',
        data: {
            labels: months,
            datasets: [
                {
                    label: 'Actual Recorded Sales',
                    data: visibleValues,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.14)',
                    fill: true,
                    tension: 0.38,
                    pointRadius: 4,
                    pointHoverRadius: 8,
                    pointHitRadius: 14,
                    pointBackgroundColor: '#059669',
                    borderWidth: 3
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            onClick: (_event, elements) => {
                if (!elements.length || !lineDetail) return;
                const index = elements[0].index;
                const amount = visibleValues[index] || 0;
                lineDetail.textContent = `${visibleLabels[index]} · ${metricType === 'revenue' ? formatDashboardMoney(amount) : `${formatDashboardNumber(amount)} units`}`;
            },
            plugins: {
                legend: { position: 'top', labels: { color: '#334155' } },
                tooltip: { callbacks: { label: (context) => metricType === 'revenue'
                    ? formatDashboardMoney(context.raw)
                    : `${formatDashboardNumber(context.raw)} units` } }
            },
            scales: {
                x: { ticks: { color: '#475569' }, grid: { color: 'rgba(15, 23, 42, 0.05)' } },
                y: { beginAtZero: true, ticks: { color: '#475569' }, grid: { color: 'rgba(15, 23, 42, 0.08)' } }
            }
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('analytics-trend-window')?.addEventListener('change', refreshAnalyticsCharts);
    document.getElementById('analytics-reset-product')?.addEventListener('click', () => {
        const filter = document.getElementById('analytics-product-filter');
        if (filter) filter.value = 'ALL';
        refreshAnalyticsCharts();
    });
});

async function handleSaveSalesEntry(event) {
    event.preventDefault();
    const payload = {
        part_id: document.getElementById("sales_part_id").value,
        month: document.getElementById("sales_month").value,
        year: document.getElementById("sales_year").value,
        units_sold: document.getElementById("sales_units").value
    };

    try {
        const response = await fetch('/api/sales', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            const modalEl = document.getElementById('recordSalesModal');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
            document.getElementById("salesEntryForm")?.reset();
            await refreshAnalyticsCharts();
        }
    } catch (err) {
        console.error("Failed to save sales record:", err);
    }
}

/* ==========================================================================
   5. WHATSAPP BILLING ENGINE & SAVED RECORDS
   ========================================================================== */

async function loadBillingProductsDropdown() {
    const dropdown = document.getElementById("billing_part_select");
    if (!dropdown) return;

    try {
        const response = await fetch('/api/inventory');
        const items = await response.json();

        dropdown.innerHTML = '<option value="" selected disabled>Choose product to add...</option>';
        items.forEach(item => {
            const opt = document.createElement("option");
            opt.value = item.id;
            opt.dataset.name = item.part_name;
            opt.dataset.price = item.unit_price;
            opt.textContent = `${item.part_name} - ₹${parseFloat(item.unit_price).toFixed(2)} (Stock: ${item.current_stock})`;
            dropdown.appendChild(opt);
        });
    } catch (err) {
        console.error("Failed to populate billing dropdown:", err);
    }
}

function addItemToInvoice() {
    const dropdown = document.getElementById("billing_part_select");
    const qtyInput = document.getElementById("billing_part_qty");
    const selectedOpt = dropdown.options[dropdown.selectedIndex];

    if (!selectedOpt || !selectedOpt.value) {
        alert("Please select a valid product first!");
        return;
    }

    const item = {
        id: selectedOpt.value,
        name: selectedOpt.dataset.name,
        price: parseFloat(selectedOpt.dataset.price),
        qty: parseInt(qtyInput.value) || 1
    };

    const existingIndex = currentInvoiceItems.findIndex(i => i.id === item.id);
    if (existingIndex > -1) {
        currentInvoiceItems[existingIndex].qty += item.qty;
    } else {
        currentInvoiceItems.push(item);
    }

    renderInvoiceTable();
}

function renderInvoiceTable() {
    const tbody = document.getElementById("invoice-items-body");
    if (!tbody) return;
    tbody.innerHTML = "";

    if (currentInvoiceItems.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted py-3">No products added to bill yet.</td></tr>`;
        updateBillTotals(0);
        return;
    }

    let subtotal = 0;
    currentInvoiceItems.forEach((item, index) => {
        const itemSubtotal = item.price * item.qty;
        subtotal += itemSubtotal;

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td class="fw-bold">${item.name}</td>
            <td>₹${item.price.toFixed(2)}</td>
            <td>${item.qty}</td>
            <td class="fw-bold">₹${itemSubtotal.toFixed(2)}</td>
            <td class="text-center">
                <button class="btn btn-sm btn-outline-danger" onclick="removeInvoiceItem(${index})">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    updateBillTotals(subtotal);
}

function removeInvoiceItem(index) {
    currentInvoiceItems.splice(index, 1);
    renderInvoiceTable();
}

function updateBillTotals(subtotal) {
    const tax = subtotal * 0.18;
    const grandTotal = subtotal + tax;

    const subEl = document.getElementById("bill-subtotal");
    const taxEl = document.getElementById("bill-tax");
    const totalEl = document.getElementById("bill-grand-total");

    if (subEl) subEl.innerText = `₹${subtotal.toFixed(2)}`;
    if (taxEl) taxEl.innerText = `₹${tax.toFixed(2)}`;
    if (totalEl) totalEl.innerText = `₹${grandTotal.toFixed(2)}`;
}

async function sendInvoiceToWhatsApp() {
    const custName = document.getElementById("cust_name").value.trim();
    const custPhone = document.getElementById("cust_phone").value.trim();
    const custVehicle = document.getElementById("cust_vehicle").value.trim();

    if (!custName || !custPhone) {
        alert("Please enter Customer Name and WhatsApp Number!");
        return;
    }

    if (currentInvoiceItems.length === 0) {
        alert("Please add at least one product to the invoice!");
        return;
    }

    let subtotal = 0;
    let itemDetailsText = "";
    
    currentInvoiceItems.forEach((item, i) => {
        const itemTotal = item.price * item.qty;
        subtotal += itemTotal;
        itemDetailsText += `${i + 1}. *${item.name}* x ${item.qty} = ₹${itemTotal.toFixed(2)}\n`;
    });

    const tax = subtotal * 0.18;
    const grandTotal = subtotal + tax;

    const invoicePayload = {
        customer_name: custName,
        customer_phone: custPhone,
        vehicle_info: custVehicle,
        subtotal: subtotal,
        tax: tax,
        grand_total: grandTotal,
        items: currentInvoiceItems
    };

    try {
        const response = await fetch('/api/invoices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(invoicePayload)
        });

        const resData = await response.json();
        if (!response.ok) throw new Error(resData.error || 'Could not save this invoice.');

        loadSavedInvoicesHistory();
        loadBillingProductsDropdown();
        const billedSubtotal = Number(resData.subtotal);
        const billedTax = Number(resData.tax);
        const billedTotal = Number(resData.grand_total);
        itemDetailsText = (resData.items || []).map((item, i) =>
            `${i + 1}. *${item.name}* x ${item.qty} = ₹${Number(item.subtotal).toFixed(2)}`
        ).join('\n') + '\n';

            const whatsappMessage = 
`*TVS PARTS & INVENTORY HUB*
----------------------------------
*OFFICIAL DIGITAL INVOICE*
*Invoice No:* ${resData.invoice_number}
*Customer Name:* ${custName}
*Phone:* ${custPhone.replace(/\D/g, '')}
${custVehicle ? `*Vehicle Info:* ${custVehicle}\n` : ""}----------------------------------
*ITEMS PURCHASED:*
${itemDetailsText}----------------------------------
*Subtotal:* ₹${billedSubtotal.toFixed(2)}
*GST (18%):* ₹${billedTax.toFixed(2)}
*GRAND TOTAL:* ₹${billedTotal.toFixed(2)}
----------------------------------
Thank you for visiting TVS Hub! Drive safely.`;

            const phoneDigits = custPhone.replace(/\D/g, "");
            const formattedPhone = phoneDigits.startsWith('91') ? phoneDigits : "91" + phoneDigits;
            const encodedUrl = `https://wa.me/${formattedPhone}?text=${encodeURIComponent(whatsappMessage)}`;
            
            window.open(encodedUrl, '_blank', 'noopener');
            clearInvoiceForm();
    } catch (err) {
        alert(err.message || "Failed to save invoice record.");
    }
}

async function loadSavedInvoicesHistory() {
    const tbody = document.getElementById("saved-invoices-tbody");
    if (!tbody) return;

    try {
        const response = await fetch('/api/invoices');
        const invoices = await response.json();

        tbody.innerHTML = "";

        if (invoices.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="text-center text-muted py-4">No saved bills found in records.</td></tr>`;
            return;
        }

        invoices.forEach(inv => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong class="text-danger">${inv.invoice_number}</strong></td>
                <td class="small text-muted">${inv.created_at}</td>
                <td class="fw-bold">${inv.customer_name}</td>
                <td>+91 ${inv.customer_phone}</td>
                <td><span class="badge bg-light text-dark border">${inv.vehicle_info || 'N/A'}</span></td>
                <td class="fw-bold text-success">₹${parseFloat(inv.grand_total).toFixed(2)}</td>
                <td class="text-end pe-4"><span class="badge bg-success"><i class="fa-solid fa-check me-1"></i> Saved & Sent</span></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error("Failed to load invoice history:", err);
    }
}

function clearInvoiceForm() {
    currentInvoiceItems = [];
    document.getElementById("cust_name").value = "";
    document.getElementById("cust_phone").value = "";
    document.getElementById("cust_vehicle").value = "";
    renderInvoiceTable();
}

/* ==========================================================================
   6. MECHANICAL WORKSHOP SERVICE HUB ENGINE
   ========================================================================== */

async function loadJobCardPartsDropdown() {
    const select = document.getElementById("job_part_select");
    if (!select) return;

    try {
        const response = await fetch('/api/inventory');
        const items = await response.json();

        select.innerHTML = '<option value="" selected disabled>Choose spare part required for repair...</option>';
        items.forEach(item => {
            const opt = document.createElement("option");
            opt.value = item.id;
            opt.dataset.name = item.part_name;
            opt.textContent = `${item.part_name} (In Stock: ${item.current_stock})`;
            select.appendChild(opt);
        });
    } catch (err) {
        console.error("Failed to load parts for job card:", err);
    }
}

async function loadJobCardsData() {
    const tbody = document.getElementById("active-jobs-tbody");
    if (!tbody) return;

    try {
        const response = await fetch('/api/jobcards');
        const jobs = await response.json();

        tbody.innerHTML = "";

        const activeJobs = jobs.filter(j => j.status === 'In Progress');
        const countBadge = document.getElementById("active-tickets-count");
        if (countBadge) countBadge.innerText = `${activeJobs.length} Active`;

        if (jobs.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-4">No service job cards logged yet.</td></tr>`;
            return;
        }

        jobs.forEach(job => {
            const isCompleted = job.status === 'Completed';
            const statusBadge = isCompleted
                ? `<span class="badge bg-success"><i class="fa-solid fa-check me-1"></i> Completed</span>`
                : `<span class="badge bg-warning text-dark"><i class="fa-solid fa-wrench me-1"></i> In Progress</span>`;

            const actionBtn = isCompleted
                ? `<button class="btn btn-sm btn-outline-secondary" disabled>Done</button>`
                : `<button class="btn btn-sm btn-success fw-bold" onclick="markJobCompleted(${job.id})"><i class="fa-solid fa-check me-1"></i> Complete Job</button>`;

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong class="text-danger">${job.job_number}</strong></td>
                <td>
                    <div class="fw-bold">${job.customer_name}</div>
                    <small class="text-muted">${job.vehicle_info}</small>
                </td>
                <td><span class="badge bg-light text-dark border">${job.mechanic_name}</span></td>
                <td>${job.part_name ? `${job.part_name} (x${job.quantity})` : '<em class="text-muted">Labor Only</em>'}</td>
                <td>${statusBadge}</td>
                <td class="text-end pe-4">${actionBtn}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error("Failed to load job cards:", err);
    }
}

async function handleCreateJobCard(event) {
    event.preventDefault();

    const partSelect = document.getElementById("job_part_select");
    const selectedOpt = partSelect.options[partSelect.selectedIndex];

    const payload = {
        customer_name: document.getElementById("job_cust_name").value,
        vehicle_info: document.getElementById("job_vehicle").value,
        mechanic_name: document.getElementById("job_mechanic").value,
        description: document.getElementById("job_description").value,
        part_id: selectedOpt && selectedOpt.value ? selectedOpt.value : null,
        part_name: selectedOpt && selectedOpt.value ? selectedOpt.dataset.name : null,
        quantity: parseInt(document.getElementById("job_part_qty").value) || 1
    };

    try {
        const response = await fetch('/api/jobcards', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            const modalEl = document.getElementById('newJobCardModal');
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
            document.getElementById("jobCardForm")?.reset();
            loadJobCardsData();
        }
    } catch (err) {
        console.error("Failed to create job card:", err);
    }
}

async function markJobCompleted(jobId) {
    try {
        const response = await fetch(`/api/jobcards/${jobId}/complete`, { method: 'PUT' });
        if (response.ok) {
            loadJobCardsData();
        }
    } catch (err) {
        console.error("Failed to mark job as completed:", err);
    }
}

/* ==========================================================================
   7. CUSTOMER VEHICLE SERVICE HISTORY LEDGER ENGINE
   ========================================================================== */

async function loadCustomerLedgerData() {
    const tbody = document.getElementById("ledger-history-tbody");
    if (!tbody) return;

    try {
        const response = await fetch('/api/customer-ledger');
        globalLedgerRecords = await response.json();

        renderLedgerTable(globalLedgerRecords);
        renderServiceDueReminders(globalLedgerRecords);

    } catch (err) {
        console.error("Failed to load customer ledger:", err);
    }
}

function renderLedgerTable(records) {
    const tbody = document.getElementById("ledger-history-tbody");
    if (!tbody) return;

    tbody.innerHTML = "";

    if (records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted py-4">No vehicle service records found.</td></tr>`;
        return;
    }

    records.forEach(rec => {
        const vehicleDisplay = rec.vehicle_info || 'Unspecified Vehicle';
        const itemDesc = rec.description || `Invoice #${rec.reference_no}`;
        const amountDisplay = rec.amount ? `₹${parseFloat(rec.amount).toFixed(2)}` : 'Job Card';

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td class="small text-muted">${rec.created_at}</td>
            <td><span class="badge bg-dark text-white font-monospace">${vehicleDisplay}</span></td>
            <td class="fw-bold text-dark">${rec.customer_name}</td>
            <td><span class="fw-semibold">${itemDesc}</span> <small class="text-muted">(${rec.source})</small></td>
            <td class="fw-bold text-success">${amountDisplay}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderServiceDueReminders(records) {
    const reminderList = document.getElementById("service-reminders-list");
    if (!reminderList) return;

    reminderList.innerHTML = "";

    const uniqueCustomers = {};
    records.forEach(r => {
        if (r.customer_name && r.customer_phone && r.customer_phone !== 'N/A') {
            uniqueCustomers[r.customer_name] = r;
        }
    });

    const customerKeys = Object.keys(uniqueCustomers);

    if (customerKeys.length === 0) {
        reminderList.innerHTML = `<div class="p-3 text-center text-muted small">No service reminders currently pending.</div>`;
        return;
    }

    customerKeys.forEach(name => {
        const item = uniqueCustomers[name];
        const vehicle = item.vehicle_info || 'TVS Vehicle';
        const phone = item.customer_phone;

        const reminderMsg = `Hello ${name}, your ${vehicle} is due for periodic maintenance service at TVS Hub. Book your slot today!`;
        const whatsappUrl = `https://wa.me/91${phone.replace(/[^0-9]/g, "")}?text=${encodeURIComponent(reminderMsg)}`;

        const div = document.createElement("div");
        div.className = "list-group-item p-3 border-bottom";
        div.innerHTML = `
            <div class="d-flex justify-content-between align-items-center mb-1">
                <strong class="text-dark">${name}</strong>
                <span class="badge bg-warning text-dark"><i class="fa-solid fa-clock me-1"></i> 6+ Months Overdue</span>
            </div>
            <div class="small text-muted mb-2">Vehicle: ${vehicle} (+91 ${phone})</div>
            <a href="${whatsappUrl}" target="_blank" class="btn btn-sm btn-success w-100 fw-bold">
                <i class="fa-brands fa-whatsapp me-1"></i> Send WhatsApp Reminder
            </a>
        `;
        reminderList.appendChild(div);
    });
}

function filterLedgerHistory() {
    const searchInput = document.getElementById("ledger-search-input");
    if (!searchInput) return;

    const query = searchInput.value.toLowerCase();
    const filtered = globalLedgerRecords.filter(r => 
        (r.vehicle_info && r.vehicle_info.toLowerCase().includes(query)) ||
        (r.customer_name && r.customer_name.toLowerCase().includes(query)) ||
        (r.reference_no && r.reference_no.toLowerCase().includes(query))
    );
    renderLedgerTable(filtered);
}

/* ==========================================================================
   8. LOCAL AI FORECAST & SMART REORDER HUB
   ========================================================================== */

/* Local AI forecast hub and inventory copilot. */
let monthlyForecastProducts = [];
let latestMonthlyForecast = null;

async function loadForecastSKUDropdown() {
    const select = document.getElementById('forecast-sku-select');
    if (!select) return;
    try {
        const [productsResponse, statusResponse] = await Promise.all([
            fetch('/api/forecast/products'),
            fetch('/api/forecast/status')
        ]);
        if (!productsResponse.ok || !statusResponse.ok) throw new Error('Could not load forecast data.');
        monthlyForecastProducts = await productsResponse.json();
        const status = await statusResponse.json();

        select.replaceChildren();
        monthlyForecastProducts.forEach((product) => {
            const option = document.createElement('option');
            option.value = product.id;
            option.textContent = `${product.part_name} · ${product.history_months} linked month(s)`;
            select.appendChild(option);
        });
        const requestedPart = new URLSearchParams(window.location.search).get('part_id');
        if (requestedPart && monthlyForecastProducts.some((product) => String(product.id) === requestedPart)) {
            select.value = requestedPart;
        }
        select.addEventListener('change', () => {
            updateMonthlyForecastChoices();
            clearMonthlyForecastResult();
        });
        updateMonthlyForecastChoices();

        const engineStatus = document.getElementById('forecast-engine-status');
        const copilotStatus = document.getElementById('copilot-status');
        if (engineStatus) {
            engineStatus.textContent = status.ai?.ready ? `AI ready · ${status.ai.model}` : 'Local AI setup needed';
            engineStatus.className = `badge py-2 px-3 ${status.ai?.ready ? 'text-bg-success' : 'text-bg-warning'}`;
        }
        if (copilotStatus) copilotStatus.textContent = status.ai?.ready
            ? `Ready · ${status.ai.model} runs locally on this computer.`
            : status.ai?.message || 'Install Ollama and download the selected local model to enable AI.';
        if (select.value) {
            currentForecastSKU = monthlyForecastProducts.find((row) => String(row.id) === String(select.value)) || null;
            const item = currentForecastSKU || {};
            document.getElementById('forecast-stock').textContent = `${item.current_stock ?? '—'} units`;
            document.getElementById('forecast-reorder-level').textContent = `${item.reorder_level ?? '—'} units`;
        }
    } catch (error) {
        showMonthlyForecastMessage(error.message || 'Could not load forecast data.', 'danger');
    }
}

function updateMonthlyForecastChoices() {
    const select = document.getElementById('forecast-sku-select');
    const analog = document.getElementById('forecast-analog');
    const manual = document.getElementById('forecast-baseline');
    const context = document.getElementById('forecast-product-context');
    if (!select || !analog) return;
    const product = monthlyForecastProducts.find((row) => String(row.id) === String(select.value));
    if (!product) return;

    analog.replaceChildren();
    const noAnalog = document.createElement('option');
    noAnalog.value = '';
    noAnalog.textContent = 'Automatic / none selected';
    analog.appendChild(noAnalog);
    monthlyForecastProducts.filter((row) => row.id !== product.id &&
        String(row.category).trim().toLowerCase() === String(product.category).trim().toLowerCase() && row.history_months >= 3)
        .forEach((peer) => {
            const option = document.createElement('option');
            option.value = peer.id;
            option.textContent = `${peer.part_name} · ${peer.history_months} months`;
            analog.appendChild(option);
        });

    if (context) {
        const lastSale = product.last_sale_date ? ` Latest sale: ${product.last_sale_date}.` : '';
        context.textContent = `${product.category} · ${product.history_months} linked month(s) of sales.${lastSale}`;
    }
    if (manual) {
        manual.disabled = product.history_months > 0;
        manual.placeholder = product.history_months > 0 ? 'Available when no sales history is linked' : 'Enter a rough monthly plan';
        manual.value = '';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('monthly-forecast-form');
    if (form) form.addEventListener('submit', triggerMLForecastSimulation);
    document.getElementById('forecast-export')?.addEventListener('click', exportMonthlyForecastCSV);
    document.getElementById('forecast-whatsapp')?.addEventListener('click', prepareSupplierWhatsAppDraft);
    document.getElementById('ai-copilot-form')?.addEventListener('submit', askInventoryCopilot);
});

async function askInventoryCopilot(event) {
    event.preventDefault();
    const input = document.getElementById('ai-copilot-question');
    const button = document.getElementById('ai-copilot-submit');
    const answerBox = document.getElementById('ai-copilot-answer');
    const question = input.value.trim();
    if (!question) return;
    button.disabled = true;
    button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Thinking';
    answerBox.classList.remove('d-none');
    answerBox.textContent = 'Checking local inventory records…';
    try {
        const response = await fetch('/api/ai/copilot', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'The local AI copilot could not answer.');
        answerBox.textContent = `${result.answer}\n\nModel: ${result.model} · no inventory changes were made.`;
    } catch (error) {
        answerBox.textContent = error.message || 'The local AI copilot could not answer.';
    } finally {
        button.disabled = false;
        button.innerHTML = '<i class="fa-solid fa-paper-plane me-2"></i>Ask copilot';
    }
}

async function triggerMLForecastSimulation(event) {
    if (event) event.preventDefault();
    const productSelect = document.getElementById('forecast-sku-select');
    if (!productSelect?.value) return;
    const runButton = document.getElementById('forecast-run-button');
    const product = monthlyForecastProducts.find((row) => String(row.id) === String(productSelect.value));
    const payload = {
        part_id: Number(productSelect.value),
        horizon: Number(document.getElementById('forecast-horizon')?.value || 6)
    };
    const analog = document.getElementById('forecast-analog')?.value;
    const baseline = document.getElementById('forecast-baseline')?.value;
    if (!product?.history_months && baseline !== '') payload.baseline_estimate = Number(baseline);
    else if (!product?.history_months && analog) payload.analog_part_id = Number(analog);

    if (runButton) runButton.disabled = true;
    try {
        const response = await fetch('/api/forecasts/monthly', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Forecast request failed.');
        currentForecastSKU = product;
        latestMonthlyForecast = data;
        renderMonthlyForecast(data);
    } catch (error) {
        latestMonthlyForecast = null;
        showMonthlyForecastMessage(error.message || 'Forecast request failed.', 'danger');
    } finally {
        if (runButton) runButton.disabled = false;
    }
}

function showMonthlyForecastMessage(message, type) {
    const box = document.getElementById('forecast-message');
    if (!box) return;
    box.className = `alert alert-${type}`;
    box.textContent = message;
}

function clearMonthlyForecastResult() {
    latestMonthlyForecast = null;
    if (forecastChartInstance) {
        forecastChartInstance.destroy();
        forecastChartInstance = null;
    }
    document.getElementById('forecast-next-month').textContent = '—';
    document.getElementById('forecast-total').textContent = '—';
    document.getElementById('forecast-mae').textContent = 'Not available';
    document.getElementById('forecast-method').textContent = '';
    document.getElementById('forecast-table-body').replaceChildren();
    document.getElementById('forecast-export').disabled = true;
    document.getElementById('forecast-whatsapp').disabled = true;
    document.getElementById('forecast-order-qty').textContent = '—';
    document.getElementById('forecast-ai-summary')?.classList.add('d-none');
    document.getElementById('forecast-mae-context').textContent = 'No AI backtest score is available yet.';
}

function renderMonthlyForecast(data) {
    const message = document.getElementById('forecast-message');
    if (data.forecast_available) {
        message.className = 'alert alert-success';
        message.textContent = `${data.method} · ${data.confidence} history support · generated ${data.generated_at || ''}`;
    } else {
        message.className = 'alert alert-warning';
        message.textContent = data.warnings?.[0] || 'A forecast is not available for this product yet.';
    }

    const warnings = document.getElementById('forecast-warnings');
    warnings.replaceChildren();
    (data.warnings || []).forEach((warning) => {
        const li = document.createElement('li');
        li.textContent = warning;
        warnings.appendChild(li);
    });
    document.getElementById('forecast-history-quality').textContent =
        `${data.history_months || 0} linked month(s) · ${data.transaction_rows || 0} valid transaction row(s) · last linked sale: ${data.last_sale_date || 'none'}`;
    document.getElementById('forecast-method').textContent = data.forecast_available
        ? `${data.method}. The final unit estimates and planning ranges were generated by the local AI model.` : '';
    const summary = document.getElementById('forecast-ai-summary');
    if (summary) {
        summary.textContent = data.ai_summary || '';
        summary.classList.toggle('d-none', !data.ai_summary);
    }
    document.getElementById('forecast-mae').textContent = 'Not available';
    document.getElementById('forecast-mae-context').textContent = 'AI forecasts have not been backtested against later recorded sales.';

    const rows = data.forecast || [];
    const total = rows.reduce((sum, row) => sum + row.units, 0);
    document.getElementById('forecast-next-month').textContent = rows.length
        ? `${rows[0].month}: ${rows[0].units} units (${rows[0].range_low}–${rows[0].range_high})` : '—';
    document.getElementById('forecast-total').textContent = data.forecast_available ? `${total} units` : '—';
    document.getElementById('forecast-mae').textContent = 'Not available';
    document.getElementById('forecast-mae-context').textContent = data.forecast_available
        ? 'AI forecast accuracy is not yet backtested against later recorded sales.'
        : 'Run an AI forecast to begin tracking performance.';

    const item = data.item || currentForecastSKU || {};
    document.getElementById('forecast-stock').textContent = `${item.current_stock ?? '—'} units`;
    document.getElementById('forecast-reorder-level').textContent = `${item.reorder_level ?? '—'} units`;
    document.getElementById('forecast-order-qty').textContent = data.reorder
        ? `${data.reorder.suggested_order_units} units` : 'Not available';
    document.getElementById('forecast-order-basis').textContent = data.reorder?.basis || 'A reorder suggestion requires a supported forecast.';

    const tbody = document.getElementById('forecast-table-body');
    tbody.replaceChildren();
    if (!data.forecast_available) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 5;
        cell.className = 'text-muted text-center py-4';
        cell.textContent = 'Choose a same-category comparable product or enter a manual planning estimate.';
        row.appendChild(cell);
        tbody.appendChild(row);
    } else {
        rows.forEach((entry) => {
            const row = document.createElement('tr');
            [entry.month, `${entry.units} units`, `${entry.range_low}–${entry.range_high} units`,
                `₹${(entry.units * Number(item.unit_price || 0)).toFixed(2)}`, entry.reason || 'Based on supplied product sales and stock data.'].forEach((value) => {
                const cell = document.createElement('td');
                cell.textContent = value;
                row.appendChild(cell);
            });
            tbody.appendChild(row);
        });
    }

    renderMonthlyForecastChart(data.history || [], rows, data.forecast_available);
    document.getElementById('forecast-export').disabled = !data.forecast_available;
    document.getElementById('forecast-whatsapp').disabled = !data.forecast_available || !data.reorder;
}

function renderMonthlyForecastChart(history, forecasts, available) {
    const canvas = document.getElementById('forecastSimulatorChart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (forecastChartInstance) forecastChartInstance.destroy();
    const historyRows = history.slice(-24);
    const labels = [...historyRows.map((row) => row.month), ...(available ? forecasts.map((row) => row.month) : [])];
    const historicalData = [...historyRows.map((row) => row.units), ...(available ? forecasts.map(() => null) : [])];
    const forecastData = [...historyRows.map(() => null), ...(available ? forecasts.map((row) => row.units) : [])];
    const lowerData = [...historyRows.map(() => null), ...(available ? forecasts.map((row) => row.range_low) : [])];
    const upperData = [...historyRows.map(() => null), ...(available ? forecasts.map((row) => row.range_high) : [])];
    forecastChartInstance = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: { labels, datasets: [
            { label: 'Recorded sales', data: historicalData, borderColor: '#315d9b', backgroundColor: 'rgba(49,93,155,.08)', fill: true, tension: .2 },
            { label: 'Monthly forecast', data: forecastData, borderColor: '#d51f2a', borderDash: [5, 4], spanGaps: true, tension: .2 },
            { label: 'Planning range high', data: upperData, borderColor: 'rgba(213,31,42,.35)', pointRadius: 0, borderDash: [2, 3], tension: .2 },
            { label: 'Planning range low', data: lowerData, borderColor: 'rgba(213,31,42,.35)', pointRadius: 0, borderDash: [2, 3], tension: .2 }
        ] },
        options: { responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: 'index' },
            scales: { y: { beginAtZero: true, title: { display: true, text: 'Units per month' } } } }
    });
}

function exportMonthlyForecastCSV() {
    if (!latestMonthlyForecast?.forecast_available) return;
    const rows = [['month', 'forecast_units', 'planning_range_low', 'planning_range_high']];
    latestMonthlyForecast.forecast.forEach((row) => rows.push([row.month, row.units, row.range_low, row.range_high]));
    const csv = rows.map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `monthly-forecast-${latestMonthlyForecast.item?.id || 'product'}.csv`;
    link.click();
    URL.revokeObjectURL(url);
}

function prepareSupplierWhatsAppDraft() {
    const phoneInput = document.getElementById('forecast-supplier-phone');
    const phone = (phoneInput?.value || '').replace(/\D/g, '');
    if (!latestMonthlyForecast?.reorder || phone.length < 7 || phone.length > 15) {
        showMonthlyForecastMessage('Enter a valid supplier phone number with 7 to 15 digits.', 'warning');
        return;
    }
    const item = latestMonthlyForecast.item || currentForecastSKU;
    const orderUnits = latestMonthlyForecast.reorder.suggested_order_units;
    const message = `Purchase order request\nProduct: ${item.part_name}\nSuggested quantity: ${orderUnits} units\nForecast method: ${latestMonthlyForecast.method}\nPlease confirm price and delivery date.`;
    window.open(`https://wa.me/${phone}?text=${encodeURIComponent(message)}`, '_blank', 'noopener');
}

/* Live operations dashboard. All figures come from the Python dashboard API. */
let dashboardSnapshot = null;
let dashboardChart = null;
let dashboardMeasure = 'units';
let dashboardRequestController = null;

async function loadInteractiveDashboard() {
    const range = document.getElementById('dashboard-range');
    const category = document.getElementById('dashboard-category');
    if (!range || !category) return;
    const errorBox = document.getElementById('dashboard-error');
    errorBox?.classList.add('d-none');
    dashboardRequestController?.abort();
    dashboardRequestController = new AbortController();
    const requestedCategory = category.value;
    const params = new URLSearchParams({ days: range.value || '30' });
    if (requestedCategory) params.set('category', requestedCategory);
    const refreshButton = document.getElementById('dashboard-refresh');
    refreshButton?.setAttribute('disabled', 'disabled');
    document.getElementById('dashboard-as-of').textContent = 'Updating from the local database…';
    try {
        const response = await fetch(`/api/dashboard/overview?${params.toString()}`, { signal: dashboardRequestController.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Dashboard data could not be loaded.');
        dashboardSnapshot = data;
        populateDashboardCategories(data.categories || [], data.category);
        renderInteractiveDashboard(data);
    } catch (error) {
        if (error.name !== 'AbortError') {
            if (errorBox) {
                errorBox.textContent = `${error.message || 'Dashboard data could not be loaded.'} Use Refresh to try again.`;
                errorBox.classList.remove('d-none');
            }
            document.getElementById('dashboard-as-of').textContent = 'The dashboard could not refresh.';
        }
    } finally {
        refreshButton?.removeAttribute('disabled');
    }
}

function populateDashboardCategories(categories, selected) {
    const select = document.getElementById('dashboard-category');
    if (!select) return;
    const current = selected === 'ALL' ? '' : selected;
    select.replaceChildren();
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'All categories';
    select.appendChild(all);
    categories.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        select.appendChild(option);
    });
    select.value = current;
}

function formatDashboardNumber(value) {
    return Number(value || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function formatDashboardMoney(value) {
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(value || 0));
}

function renderInteractiveDashboard(data) {
    const inventory = data.inventory || {};
    const sales = data.sales || {};
    const workshop = data.workshop || {};
    const asOf = new Date(`${data.as_of}T12:00:00`);
    document.getElementById('dashboard-as-of').textContent = `As of ${asOf.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })} · ${data.category === 'ALL' ? 'all categories' : data.category} · ${Number(data.days)}-day comparison`;
    document.getElementById('dash-kpi-products').textContent = formatDashboardNumber(inventory.items);
    document.getElementById('dash-kpi-low-stock').textContent = formatDashboardNumber(inventory.low_stock_items);
    document.getElementById('dash-kpi-stock-detail').textContent = `${formatDashboardNumber(inventory.out_of_stock_items)} out of stock · ${formatDashboardNumber(inventory.negative_stock_items)} negative balance(s)`;
    document.getElementById('dash-kpi-sales').textContent = formatDashboardNumber(sales.current_units);
    const change = document.getElementById('dash-kpi-sales-change');
    if (sales.change_percent == null) {
        change.textContent = `${formatDashboardMoney(sales.current_revenue)} recorded value · prior period has no units for comparison`;
    } else {
        const direction = sales.change_percent > 0 ? 'up' : sales.change_percent < 0 ? 'down' : 'unchanged';
        change.textContent = `${Math.abs(sales.change_percent)}% ${direction} vs previous equal period · ${formatDashboardMoney(sales.current_revenue)} value`;
    }
    document.getElementById('dash-kpi-stock-value').textContent = formatDashboardMoney(inventory.stock_value_at_list_price);
    document.getElementById('dash-kpi-open-jobs').textContent = `${formatDashboardNumber(workshop.open_jobs)} open workshop job(s)`;
    document.getElementById('dash-sales-row-chip').textContent = `Saved transactions in period: ${formatDashboardNumber(sales.current_rows)}`;
    document.getElementById('dash-linked-row-chip').textContent = `Product-linked: ${formatDashboardNumber(sales.linked_current_rows)}`;
    document.getElementById('dash-period-revenue').textContent = `Recorded value: ${formatDashboardMoney(sales.current_revenue)}`;
    renderDashboardChart(data);
    renderDashboardInsights(data.insights || []);
    renderDashboardTopProducts(data.top_products || []);
    renderDashboardRestock(data.restock_priorities || []);
    renderDashboardDataQuality(data);
}

function renderDashboardChart(data) {
    const canvas = document.getElementById('dashboard-sales-chart');
    if (!canvas || typeof Chart === 'undefined') return;
    if (dashboardChart) dashboardChart.destroy();
    const series = data.sales?.series || {};
    const unitMode = dashboardMeasure === 'units';
    const current = unitMode ? series.current : series.current_revenue;
    const previous = unitMode ? series.previous : series.previous_revenue;
    const currentLabel = `Selected ${Number(data.days)} days`;
    const previousLabel = `Previous ${Number(data.days)} days`;
    dashboardChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: { labels: series.labels || [], datasets: [
            { label: currentLabel, data: current || [], backgroundColor: 'rgba(205, 31, 42, .78)', borderRadius: 5, maxBarThickness: 28 },
            { label: previousLabel, data: previous || [], backgroundColor: 'rgba(100, 116, 139, .38)', borderRadius: 5, maxBarThickness: 28 }
        ] },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${unitMode ? `${formatDashboardNumber(context.raw)} units` : formatDashboardMoney(context.raw)}` } } },
            scales: {
                x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 12 } },
                y: { beginAtZero: true, title: { display: true, text: unitMode ? 'Units' : 'Recorded value (₹)' }, ticks: { callback: (value) => unitMode ? formatDashboardNumber(value) : formatDashboardMoney(value) } }
            }
        }
    });
    document.getElementById('dash-chart-caption').textContent = `${unitMode ? 'Units sold' : 'Sales value from saved sale rows'} · selected period compared with the previous equal-length period`;
}

function renderDashboardInsights(insights) {
    const container = document.getElementById('dashboard-insights');
    container.replaceChildren();
    if (!insights.length) {
        const empty = document.createElement('p');
        empty.className = 'small text-muted mb-0';
        empty.textContent = 'No additional signals from the available records.';
        container.appendChild(empty);
        return;
    }
    insights.forEach((insight) => {
        const level = ['danger', 'warning', 'success', 'info', 'secondary'].includes(insight.level) ? insight.level : 'secondary';
        const card = document.createElement('article');
        card.className = `dash-insight ${level} rounded-3 p-3`;
        const title = document.createElement('div');
        title.className = 'fw-semibold small mb-1';
        title.textContent = insight.title || 'Data signal';
        const detail = document.createElement('div');
        detail.className = 'small text-muted';
        detail.textContent = insight.detail || '';
        card.append(title, detail);
        container.appendChild(card);
    });
}

function renderDashboardTopProducts(products) {
    const body = document.getElementById('dashboard-top-products');
    body.replaceChildren();
    const query = (document.getElementById('dashboard-product-search')?.value || '').trim().toLocaleLowerCase();
    const matches = products.filter((item) => `${item.part_name} ${item.category}`.toLocaleLowerCase().includes(query));
    if (!matches.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 5;
        cell.className = 'text-center text-muted py-4';
        cell.textContent = products.length ? 'No top product matches this search.' : 'No linked product sales were recorded in this period.';
        row.appendChild(cell);
        body.appendChild(row);
        return;
    }
    matches.forEach((item) => {
        const row = document.createElement('tr');
        const name = document.createElement('td');
        name.className = 'fw-semibold';
        name.textContent = item.part_name;
        const category = document.createElement('td');
        category.textContent = item.category || 'Uncategorized';
        const units = document.createElement('td');
        units.className = 'text-end fw-semibold';
        units.textContent = formatDashboardNumber(item.units);
        const revenue = document.createElement('td');
        revenue.className = 'text-end';
        revenue.textContent = formatDashboardMoney(item.revenue);
        const action = document.createElement('td');
        action.className = 'text-end';
        const link = document.createElement('a');
        link.className = 'btn btn-sm btn-outline-danger';
        link.href = `/forecast-hub?part_id=${encodeURIComponent(item.part_id)}`;
        link.textContent = 'Forecast';
        action.appendChild(link);
        row.append(name, category, units, revenue, action);
        body.appendChild(row);
    });
}

function renderDashboardRestock(items) {
    const body = document.getElementById('dashboard-restock');
    body.replaceChildren();
    if (!items.length) {
        const row = document.createElement('tr');
        const cell = document.createElement('td');
        cell.colSpan = 4;
        cell.className = 'text-center text-muted py-4';
        cell.textContent = 'No products are at or below their reorder level.';
        row.appendChild(cell);
        body.appendChild(row);
        return;
    }
    items.forEach((item) => {
        const row = document.createElement('tr');
        const name = document.createElement('td');
        name.className = 'fw-semibold';
        name.textContent = item.part_name;
        const stock = document.createElement('td');
        stock.textContent = formatDashboardNumber(item.current_stock);
        const gap = document.createElement('td');
        gap.className = 'fw-semibold';
        gap.textContent = `${formatDashboardNumber(item.stock_gap)} units`;
        const signal = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `badge ${item.risk === 'Negative balance' || item.risk === 'Out of stock' ? 'text-bg-danger' : 'text-bg-warning'}`;
        badge.textContent = item.risk;
        signal.appendChild(badge);
        row.append(name, stock, gap, signal);
        body.appendChild(row);
    });
}

function renderDashboardDataQuality(data) {
    const quality = data.data_quality || {};
    const allRows = Number(quality.sales_rows_total || 0);
    const unmapped = Number(quality.unmapped_sales_rows || 0);
    const linked = Math.max(0, allRows - unmapped);
    const invalid = Number(quality.invalid_rows_in_range || 0);
    let message = `Across the database, ${formatDashboardNumber(linked)} of ${formatDashboardNumber(allRows)} sales rows link to current inventory IDs.`;
    if (unmapped) message += ` ${formatDashboardNumber(unmapped)} unlinked row(s) remain in recorded sales totals but are excluded from product rankings and item forecasts.`;
    message += ' Stock at list price uses positive on-hand quantity × list price; it is not a cost valuation.';
    if (invalid) message += ` ${formatDashboardNumber(invalid)} invalid row(s) were skipped in this period.`;
    document.getElementById('dashboard-data-quality').textContent = message;
}

function exportInteractiveDashboardCSV() {
    if (!dashboardSnapshot) return;
    const data = dashboardSnapshot;
    const rows = [
        ['Section', 'Metric', 'Value'],
        ['Dashboard', 'As of', data.as_of],
        ['Dashboard', 'Period days', data.days],
        ['Dashboard', 'Category', data.category],
        ['Inventory', 'Product count', data.inventory.items],
        ['Inventory', 'Low stock products', data.inventory.low_stock_items],
        ['Inventory', 'Out of stock products', data.inventory.out_of_stock_items],
        ['Inventory', 'Negative stock products', data.inventory.negative_stock_items],
        ['Inventory', 'Positive stock at list price', data.inventory.stock_value_at_list_price],
        ['Sales', 'Current period units', data.sales.current_units],
        ['Sales', 'Previous equal period units', data.sales.previous_units],
        ['Sales', 'Current period recorded value', data.sales.current_revenue],
        ['Sales', 'Previous equal period recorded value', data.sales.previous_revenue],
        ['Sales', 'Current period transaction rows', data.sales.current_rows],
        ['Sales', 'Current period product-linked rows', data.sales.linked_current_rows],
        ...data.top_products.map((item) => ['Top product', item.part_name, `${item.units} units; value ${item.revenue}`]),
        ...data.restock_priorities.map((item) => ['Restock priority', item.part_name, `${item.stock_gap} units to reorder level; ${item.risk}`])
    ];
    const csv = rows.map((row) => row.map((value) => `"${String(value ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `tvs-dashboard-${data.as_of}.csv`;
    link.click();
    URL.revokeObjectURL(url);
}

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('dashboard-range')) return;
    document.getElementById('dashboard-range').addEventListener('change', loadInteractiveDashboard);
    document.getElementById('dashboard-category').addEventListener('change', loadInteractiveDashboard);
    document.getElementById('dashboard-refresh').addEventListener('click', loadInteractiveDashboard);
    document.getElementById('dashboard-export').addEventListener('click', exportInteractiveDashboardCSV);
    document.getElementById('dashboard-product-search').addEventListener('input', () => {
        if (dashboardSnapshot) renderDashboardTopProducts(dashboardSnapshot.top_products || []);
    });
    document.querySelectorAll('[data-dashboard-measure]').forEach((button) => button.addEventListener('click', () => {
        dashboardMeasure = button.dataset.dashboardMeasure;
        document.querySelectorAll('[data-dashboard-measure]').forEach((candidate) => {
            const active = candidate === button;
            candidate.classList.toggle('active', active);
            candidate.classList.toggle('btn-danger', active);
            candidate.classList.toggle('btn-outline-danger', !active);
            candidate.setAttribute('aria-pressed', String(active));
        });
        if (dashboardSnapshot) renderDashboardChart(dashboardSnapshot);
    }));
});
