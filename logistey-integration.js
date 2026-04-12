/**
 * Logistey — API Integration
 * Drop this file in your website folder and add to demo.html:
 * <script src="logistey-integration.js"></script>
 * 
 * Make sure BASE_URL points to your Flask server (ngrok URL when local)
 */

const BASE_URL = "https://playset-budget-parched.ngrok-free.dev"; // ← update this to your ngrok/server URL

// ── FETCH ALL DATA ─────────────────────────────────────────────────────────────

async function fetchAllData() {
  try {
    const res = await fetch(`${BASE_URL}/export/all`);
    const data = await res.json();
    if (!data.success) throw new Error("API error");
    return data;
  } catch (e) {
    console.error("Logistey API error:", e);
    return null;
  }
}

// ── DASHBOARD STATS ────────────────────────────────────────────────────────────

async function loadDashboardStats() {
  try {
    const res = await fetch(`${BASE_URL}/api/stats`);
    const data = await res.json();
    if (!data.success) return;
    const s = data.stats;

    // Update stat cards
    const cards = document.querySelectorAll(".stat-card .stat-val");
    if (cards[0]) cards[0].textContent = s.total_orders;
    if (cards[1]) cards[1].textContent = `₹${(s.total_revenue / 1000).toFixed(0)}K`;
    if (cards[2]) cards[2].textContent = countLowStock();
    if (cards[3]) cards[3].textContent = s.pending_orders;
  } catch (e) {
    console.error("Stats load error:", e);
  }
}

// ── INVENTORY ──────────────────────────────────────────────────────────────────

function countLowStock() {
  return window._inventoryData
    ? window._inventoryData.filter(i => i.quantity <= i.reorder_threshold).length
    : "—";
}

async function loadInventory() {
  try {
    const res = await fetch(`${BASE_URL}/api/inventory`);
    const data = await res.json();
    if (!data.success) return;
    window._inventoryData = data.inventory;

    const tbody = document.getElementById("invBody");
    if (!tbody) return;

    tbody.innerHTML = data.inventory.map(item => {
      const pct = Math.min(100, Math.round((item.quantity / (item.reorder_threshold * 5)) * 100));
      const isLow = item.quantity <= item.reorder_threshold;
      const isCritical = item.quantity <= item.reorder_threshold * 0.5;

      let statusBadge, statusClass;
      if (isCritical) {
        statusBadge = "⏳ Reordering";
        statusClass = "voice-pending";
      } else if (isLow) {
        statusBadge = "⚠️ Low Stock";
        statusClass = "forecasted";
      } else {
        statusBadge = "✓ In Stock";
        statusClass = "monitoring";
      }

      const barColor = isCritical ? "var(--danger)" : isLow ? "var(--warning)" : "var(--success)";

      return `<tr>
        <td><strong>${item.item.charAt(0).toUpperCase() + item.item.slice(1)}</strong></td>
        <td>Agricultural</td>
        <td>${item.quantity} kg</td>
        <td>₹${item.price_per_unit}</td>
        <td>₹${(item.quantity * item.price_per_unit).toLocaleString("en-IN")}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <div class="prog-bar"><div class="prog-fill" style="width:${pct}%;background:${barColor}"></div></div>
            <span class="sys-badge ${statusClass}">${statusBadge}</span>
          </div>
        </td>
      </tr>`;
    }).join("");

    // Also update low stock widget in dashboard
    updateLowStockWidget(data.inventory);
  } catch (e) {
    console.error("Inventory load error:", e);
  }
}

function updateLowStockWidget(inventory) {
  const lowItems = inventory
    .filter(i => i.quantity <= i.reorder_threshold * 2)
    .sort((a, b) => (a.quantity / a.reorder_threshold) - (b.quantity / b.reorder_threshold))
    .slice(0, 4);

  const widget = document.querySelector(".widget .widget-body .stock-item");
  if (!widget) return;

  const parent = widget.parentElement;
  parent.innerHTML = lowItems.map(item => {
    const pct = Math.min(100, Math.round((item.quantity / (item.reorder_threshold * 3)) * 100));
    const isLow = item.quantity <= item.reorder_threshold;
    const barColor = isLow ? "var(--danger)" : "var(--warning)";
    const badgeClass = isLow ? "badge-red" : "badge-yellow";
    const emoji = { onions: "🧅", tomatoes: "🍅", rice: "🌾", potatoes: "🥔", lentils: "🫘", wheat: "🌾" }[item.item] || "📦";

    return `<div class="stock-item">
      <span style="font-size:1.1rem">${emoji}</span>
      <span class="stock-name">${item.item.charAt(0).toUpperCase() + item.item.slice(1)}</span>
      <div class="stock-bar-wrap">
        <div class="stock-bar-inner" style="width:${pct}%;background:${barColor}"></div>
      </div>
      <span class="badge ${badgeClass}">${item.quantity} kg${isLow ? " ⚠️" : ""}</span>
    </div>`;
  }).join("");
}

// ── ORDERS ─────────────────────────────────────────────────────────────────────

async function loadOrders() {
  try {
    const res = await fetch(`${BASE_URL}/api/orders`);
    const data = await res.json();
    if (!data.success) return;

    // Update orders stats bar
    const total = data.count;
    const pending = data.orders.filter(o => o.status === "pending").length;
    const dispatched = data.orders.filter(o => ["dispatched", "out_for_delivery"].includes(o.status)).length;
    const delivered = data.orders.filter(o => o.status === "delivered").length;

    const oStats = document.querySelectorAll(".o-stat .num");
    if (oStats[0]) oStats[0].textContent = total;
    if (oStats[1]) oStats[1].textContent = pending;
    if (oStats[2]) oStats[2].textContent = dispatched;
    if (oStats[3]) oStats[3].textContent = delivered;

    // Update orders table
    const tbody = document.querySelector("#panel-orders .data-table tbody");
    if (!tbody) return;

    const statusMap = {
      pending: { label: "Pending", cls: "badge-orange" },
      confirmed: { label: "Confirmed", cls: "badge-blue" },
      dispatched: { label: "In Transit", cls: "badge-yellow" },
      out_for_delivery: { label: "Out for Delivery", cls: "badge-yellow" },
      delivered: { label: "Delivered ✓", cls: "badge-green" },
      cancelled: { label: "Cancelled", cls: "badge-red" },
    };

    tbody.innerHTML = data.orders.map(o => {
      const s = statusMap[o.status] || { label: o.status, cls: "badge-orange" };
      const date = o.created_at ? o.created_at.slice(0, 10) : "—";
      const isDelivered = o.status === "delivered" || o.status === "cancelled";

      return `<tr>
        <td><strong style="color:var(--primary)">${o.order_ref}</strong></td>
        <td>${o.caller_phone || "—"}</td>
        <td>${o.item ? o.item.charAt(0).toUpperCase() + o.item.slice(1) : "—"} × ${o.quantity || "?"} kg</td>
        <td><strong>₹${(o.total_price || 0).toLocaleString("en-IN")}</strong></td>
        <td>${date}</td>
        <td><span class="badge ${s.cls}">${s.label}</span></td>
        <td style="display:flex;gap:6px">
          ${isDelivered
          ? `<button class="btn btn-secondary btn-sm" onclick="showToast('Order ${o.order_ref} — ${s.label}')">Details</button>`
          : `<button class="btn btn-ghost btn-sm" onclick="showToast('Order ${o.order_ref}: ${s.label} — ETA ${o.estimated_delivery || "TBD"}')">Track</button>`
        }
          <button class="btn btn-secondary btn-sm" onclick="openBill('${o.order_ref}')">Invoice</button>
        </td>
      </tr>`;
    }).join("");

    // Update recent orders widget on dashboard
    updateRecentOrdersWidget(data.orders.slice(0, 4));
  } catch (e) {
    console.error("Orders load error:", e);
  }
}

function updateRecentOrdersWidget(orders) {
  const widget = document.querySelector(".widget .order-row");
  if (!widget) return;
  const parent = widget.parentElement;

  const statusMap = {
    pending: { label: "Processing", cls: "badge-orange" },
    confirmed: { label: "Confirmed", cls: "badge-blue" },
    dispatched: { label: "In Transit", cls: "badge-yellow" },
    out_for_delivery: { label: "Out for Delivery", cls: "badge-yellow" },
    delivered: { label: "Delivered ✓", cls: "badge-green" },
    cancelled: { label: "Cancelled", cls: "badge-red" },
  };

  parent.innerHTML = orders.map(o => {
    const s = statusMap[o.status] || { label: o.status, cls: "badge-orange" };
    return `<div class="order-row">
      <span class="order-id-badge">${o.order_ref.slice(-8)}</span>
      <span class="order-cust">${o.caller_phone || "—"}</span>
      <span class="badge ${s.cls}">${s.label}</span>
      <span class="order-amt">₹${(o.total_price || 0).toLocaleString("en-IN")}</span>
    </div>`;
  }).join("");
}

// ── BILLS ──────────────────────────────────────────────────────────────────────

async function openBill(orderRef) {
  try {
    const res = await fetch(`${BASE_URL}/api/bills`);
    const data = await res.json();
    const bill = data.bills.find(b => b.order_ref === orderRef);
    if (bill) {
      window.open(`${BASE_URL}/bill/${bill.bill_ref}`, "_blank");
    } else {
      showToast("No bill found for this order yet.");
    }
  } catch (e) {
    showToast("Could not fetch bill.");
  }
}

// ── CALL LOGS → VOICE LOGS PANEL ──────────────────────────────────────────────

async function loadCallLogs() {
  try {
    const res = await fetch(`${BASE_URL}/api/calls`);
    const data = await res.json();
    if (!data.success) return;

    const intentMap = {
      stock_arrival: { icon: "🚚", color: "#27AE60", label: "Stock Arrival", badge: "✓ Stock Updated" },
      stock_query: { icon: "🔍", color: "#2471A3", label: "Stock Query", badge: "ℹ Stock Info Sent" },
      price_offer: { icon: "💰", color: "#D68910", label: "Price Offer", badge: "🤝 Negotiation Active" },
      deal_confirm: { icon: "✅", color: "#1a9450", label: "Deal Confirmed", badge: "✅ Deal Locked" },
      order_placed: { icon: "📦", color: "#8E44AD", label: "Order Placed", badge: "📦 Order & Bill Created" },
      order_status: { icon: "📍", color: "#2471A3", label: "Order Status", badge: "📍 Status Sent" },
      delivery_query: { icon: "🚚", color: "#D68910", label: "Delivery Query", badge: "🚚 ETA Provided" },
      cancel_order: { icon: "❌", color: "#C0392B", label: "Order Cancelled", badge: "❌ Cancelled" },
      unknown: { icon: "❓", color: "#7A6055", label: "Unknown", badge: "🤷 Unclear Intent" },
    };

    // Update demand ticker from recent calls
    updateDemandTicker(data.calls);
  } catch (e) {
    console.error("Call logs error:", e);
  }
}

function updateDemandTicker(calls) {
  const itemCounts = {};
  calls.forEach(c => {
    if (c.item) itemCounts[c.item] = (itemCounts[c.item] || 0) + 1;
  });

  const total = calls.length || 1;
  const items = ["onions", "tomatoes", "rice", "potatoes", "lentils", "wheat"];
  const tickers = {
    onions: document.getElementById("maggiDemand"),
    tomatoes: document.getElementById("riceDemand"),
    rice: document.getElementById("oilDemand"),
    potatoes: document.getElementById("bisDemand"),
  };

  Object.entries(tickers).forEach(([item, el]) => {
    if (!el) return;
    const count = itemCounts[item] || 0;
    const pct = Math.round((count / total) * 100);
    el.textContent = pct > 0 ? `+${pct}% ↑` : "— No calls";
    el.style.color = pct > 20 ? "#FF5555" : pct > 10 ? "#58D68D" : "rgba(255,255,255,0.4)";
  });
}

// ── CUSTOMERS / BUYERS ─────────────────────────────────────────────────────────

async function loadBuyers() {
  try {
    const res = await fetch(`${BASE_URL}/api/buyers`);
    const data = await res.json();
    if (!data.success) return;

    const tbody = document.querySelector("#panel-customers .data-table tbody");
    if (!tbody) return;

    const colors = ["#C4622D", "#2980B9", "#27AE60", "#8E44AD", "#D35400"];

    tbody.innerHTML = data.buyers.map((b, i) => {
      const initial = (b.buyer_name || b.buyer_phone || "?")[0].toUpperCase();
      const color = colors[i % colors.length];
      const creditUsed = b.outstanding_balance || 0;
      const creditPct = b.credit_limit ? Math.round((creditUsed / b.credit_limit) * 100) : 0;
      const isOverLimit = creditPct >= 100;

      return `<tr>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <div style="width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,${color},${color}cc);display:flex;align-items:center;justify-content:center;font-size:0.75rem;font-weight:700;color:white;flex-shrink:0">${initial}</div>
            <strong>${b.buyer_name || "Unknown"}</strong>
          </div>
        </td>
        <td>${b.address || "—"}</td>
        <td>${b.buyer_phone}</td>
        <td>—</td>
        <td>
          <strong>₹${creditUsed.toLocaleString("en-IN")}</strong>
          <div style="font-size:0.72rem;color:var(--text-muted)">of ₹${(b.credit_limit || 0).toLocaleString("en-IN")} limit</div>
          <div class="prog-bar" style="margin-top:4px">
            <div class="prog-fill" style="width:${Math.min(100, creditPct)}%;background:${isOverLimit ? "var(--danger)" : creditPct > 75 ? "var(--warning)" : "var(--success)"}"></div>
          </div>
        </td>
        <td>${b.cycle_due_date || "—"}</td>
        <td><span class="badge ${isOverLimit ? "badge-red" : "badge-green"}">${isOverLimit ? "Over Limit" : "Active"}</span></td>
        <td><button class="btn btn-ghost btn-sm" onclick="showToast('${b.buyer_name || b.buyer_phone} — Balance: ₹${creditUsed}')">View</button></td>
      </tr>`;
    }).join("");
  } catch (e) {
    console.error("Buyers error:", e);
  }
}

// ── REORDER LOG ────────────────────────────────────────────────────────────────

async function loadReorderLog() {
  try {
    const res = await fetch(`${BASE_URL}/api/reorder-log`);
    const data = await res.json();
    if (!data.success) return;
    // Surface reorder count in notifications
    const pendingReorders = data.reorders.filter(r => r.status === "called").length;
    if (pendingReorders > 0) {
      showToast(`🔄 ${pendingReorders} supplier reorder call(s) placed today`);
    }
  } catch (e) {
    console.error("Reorder log error:", e);
  }
}

// ── AUTO REFRESH ───────────────────────────────────────────────────────────────

function initLogistey() {
  console.log("🚚 Logistey Integration — Connecting to", BASE_URL);

  // Load all data on startup
  loadInventory();
  loadOrders();
  loadDashboardStats();
  loadCallLogs();
  loadBuyers();
  loadReorderLog();

  // Refresh every 30 seconds
  setInterval(() => {
    loadInventory();
    loadOrders();
    loadDashboardStats();
    loadCallLogs();
  }, 30000);

  // Wire up the refresh button in topbar
  const refreshBtn = document.querySelector('.icon-btn[title="Refresh"]');
  if (refreshBtn) {
    refreshBtn.onclick = () => {
      loadInventory();
      loadOrders();
      loadDashboardStats();
      loadCallLogs();
      loadBuyers();
      showToast("Dashboard refreshed with live data! 🔄");
    };
  }

  // Wire up export button in inventory
  const exportBtn = document.querySelector('.page-header-actions .btn-secondary');
  if (exportBtn && exportBtn.textContent.includes("Export")) {
    exportBtn.onclick = () => window.open(`${BASE_URL}/export/orders.csv`, "_blank");
  }
}

// Auto-init when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initLogistey);
} else {
  initLogistey();
}
