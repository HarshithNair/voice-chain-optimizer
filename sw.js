// Logistey Service Worker — Network-First Strategy
// API responses and HTML pages always come from the network (live data).
// Static assets (CSS, JS, fonts) are cached for speed but refreshed on change.

const CACHE_NAME = 'logistey-v3';

// These static assets are safe to cache (they rarely change)
const STATIC_ASSETS = [
  './style.css',
  './cursor.js',
  './manifest.json',
];

// These paths must NEVER be served from cache
const NEVER_CACHE = [
  '/api/',
  '/dashboard',
  '/orders',
  '/export/',
  '/bill/',
  '/voice',
  '/handle-recording',
];

function shouldNeverCache(url) {
  const path = new URL(url).pathname;
  return NEVER_CACHE.some(p => path.startsWith(p));
}

// Install: cache only safe static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

// Activate: delete all old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Fetch: Network-First for HTML pages & APIs, Cache-First for static assets
self.addEventListener('fetch', event => {
  const url = event.request.url;

  // Always go network-first for API routes and HTML pages
  if (shouldNeverCache(url) || event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() => {
        // If network fails for a navigation, show a simple offline message
        if (event.request.mode === 'navigate') {
          return new Response(
            '<h2>Offline — please check your connection</h2>',
            { headers: { 'Content-Type': 'text/html' } }
          );
        }
      })
    );
    return;
  }

  // Cache-First for static assets
  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});
