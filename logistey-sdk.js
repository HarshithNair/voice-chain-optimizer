/**
 * Logistey Website Integration SDK
 * Drop this into any HTML page or React/Vue/Next.js app.
 * 
 * Usage:
 *   const gpt = new Logistey("https://your-server.com");
 *   const inv = await gpt.getInventory();
 */

class Logistey {
  // Pass a baseUrl only when calling from a different origin.
  // When used from Flask-served pages, leave it empty ("") for relative URLs.
  constructor(baseUrl = "") {
    this.base = baseUrl.replace(/\/$/, "");
  }

  async _fetch(path, options = {}) {
    const res = await fetch(`${this.base}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    if (!res.ok) throw new Error(`Logistey API error: ${res.status}`);
    return res.json();
  }

  // ── Inventory ──────────────────────────────────────
  getInventory() {
    return this._fetch("/api/inventory");
  }

  updateInventory(item, { price_per_unit, quantity_delta, min_price } = {}) {
    return this._fetch(`/api/inventory/${item}`, {
      method: "PATCH",
      body: JSON.stringify({ price_per_unit, quantity_delta, min_price }),
    });
  }

  // ── Orders ─────────────────────────────────────────
  getOrders({ status, phone, item } = {}) {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (phone) params.set("phone", phone);
    if (item) params.set("item", item);
    return this._fetch(`/api/orders?${params}`);
  }

  getOrder(orderRef) {
    return this._fetch(`/api/orders/${orderRef}`);
  }

  createOrder({ phone, item, quantity, price_per_unit, address, notes }) {
    return this._fetch("/api/orders", {
      method: "POST",
      body: JSON.stringify({ phone, item, quantity, price_per_unit, address, notes }),
    });
  }

  updateOrderStatus(orderRef, { status, note, updated_by = "admin" }) {
    return this._fetch(`/api/orders/${orderRef}/status`, {
      method: "POST",
      body: JSON.stringify({ status, note, updated_by }),
    });
  }

  // ── Stats & Calls ──────────────────────────────────
  getStats() {
    return this._fetch("/api/stats");
  }

  getCallLogs() {
    return this._fetch("/api/calls");
  }
}

// ── React Hook (copy into your React project) ────────
// import { useState, useEffect } from "react";
//
// export function useLogistey(baseUrl) {
//   const gpt = new Logistey(baseUrl);
//   const [inventory, setInventory] = useState([]);
//   const [orders, setOrders] = useState([]);
//   const [stats, setStats] = useState(null);
//   const [loading, setLoading] = useState(true);
//
//   const refresh = async () => {
//     setLoading(true);
//     const [inv, ord, st] = await Promise.all([
//       gpt.getInventory(), gpt.getOrders(), gpt.getStats()
//     ]);
//     setInventory(inv.inventory);
//     setOrders(ord.orders);
//     setStats(st.stats);
//     setLoading(false);
//   };
//
//   useEffect(() => { refresh(); }, []);
//   return { inventory, orders, stats, loading, refresh, gpt };
// }

// Export for different module systems
// CommonJS (Node.js)
if (typeof module !== "undefined" && typeof module.exports !== "undefined") {
  module.exports = Logistey;
}
// Browser global
if (typeof window !== "undefined") {
  window.Logistey = Logistey;
}
// Note: for ES Module import, use: import Logistey from './logistey-sdk.js'
// (requires <script type="module"> or a bundler like Vite/Webpack)