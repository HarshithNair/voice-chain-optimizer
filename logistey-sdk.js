/**
 * Logistey JavaScript SDK
 * A lightweight client for interacting with the Logistey Flask API.
 * Usage:
 *   const sdk = new LogisteySdk("https://your-ngrok-url.ngrok-free.dev");
 */

class LogisteySdk {
  constructor(baseUrl) {
    this.baseUrl = baseUrl ? baseUrl.replace(/\/$/, "") : "";
  }

  async _fetch(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return res.json();
  }

  // ── INVENTORY ──────────────────────────────────────

  /** Get all inventory items */
  getInventory() {
    return this._fetch("/api/inventory");
  }

  /**
   * Update price, quantity, or min_price for an item
   * @param {string} item - e.g. "onions"
   * @param {{ price_per_unit?, quantity_delta?, min_price? }} data
   */
  updateInventory(item, data) {
    return this._fetch(`/api/inventory/${encodeURIComponent(item)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  // ── ORDERS ────────────────────────────────────────

  /**
   * List orders with optional filters
   * @param {{ status?, phone?, item? }} filters
   */
  getOrders({ status, phone, item } = {}) {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (phone) params.set("phone", phone);
    if (item) params.set("item", item);
    return this._fetch(`/api/orders?${params}`);
  }

  /**
   * Get full details + delivery timeline for a single order
   * @param {string} orderRef - e.g. "GDN-2026-ABCD12"
   */
  getOrder(orderRef) {
    return this._fetch(`/api/orders/${encodeURIComponent(orderRef)}`);
  }

  /**
   * Create a new order from the website (non-voice)
   * @param {{ phone, item, quantity, price_per_unit, address?, notes? }} data
   */
  createOrder(data) {
    return this._fetch("/api/orders", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  /**
   * Update the status of an order
   * @param {string} orderRef
   * @param {{ status, note?, updated_by? }} data
   */
  updateOrderStatus(orderRef, data) {
    return this._fetch(`/api/orders/${encodeURIComponent(orderRef)}/status`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // ── CALLS & STATS ─────────────────────────────────

  /** Get recent call logs */
  getCalls() {
    return this._fetch("/api/calls");
  }

  /** Get dashboard summary stats */
  getStats() {
    return this._fetch("/api/stats");
  }

  // ── BUYERS ────────────────────────────────────────

  /** Get all registered buyers */
  getBuyers() {
    return this._fetch("/api/buyers");
  }

  // ── BILLS ─────────────────────────────────────────

  /** Get all bills */
  getBills() {
    return this._fetch("/api/bills");
  }

  // ── REORDER LOG ────────────────────────────────────

  /** Get the reorder log */
  getReorderLog() {
    return this._fetch("/api/reorder-log");
  }

  // ── SUPPLIER CONFIRMATION ─────────────────────────

  /**
   * Trigger an outbound supplier confirmation call for an order
   * @param {string} orderRef
   */
  triggerSupplierConfirm(orderRef) {
    return this._fetch(`/api/supplier-confirm/${encodeURIComponent(orderRef)}`, {
      method: "POST",
    });
  }

  /**
   * Test a supplier confirmation call to any phone
   * @param {{ phone, order_ref? }} data
   */
  testSupplierConfirm(data) {
    return this._fetch("/api/test-supplier-confirm", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // ── EXPORT ────────────────────────────────────────

  /** Open CSV export for orders in a new browser tab */
  exportOrdersCsv() {
    window.open(`${this.baseUrl}/export/orders.csv`, "_blank");
  }

  /** Open CSV export for inventory in a new browser tab */
  exportInventoryCsv() {
    window.open(`${this.baseUrl}/export/inventory.csv`, "_blank");
  }

  /** Get all data as a single JSON export */
  exportAll() {
    return this._fetch("/export/all");
  }
}

// Auto-expose if used as a plain script tag (non-module)
if (typeof window !== "undefined") {
  window.LogisteySdk = LogisteySdk;
}