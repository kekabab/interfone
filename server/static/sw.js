const CACHE_NAME = 'interfone-ai-v5';
const ASSETS = [
    '/manifest.json'
    // NÃO cacheamos '/' (index.html) propositalmente.
    // Assim o HTML principal é sempre buscado da rede, garantindo atualizações.
];

self.addEventListener('install', (event) => {
    self.skipWaiting(); // Ativa o novo SW imediatamente, sem esperar tabs fecharem
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS);
        })
    );
});

// A página pode pedir pra pular a fila de espera via postMessage
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                })
            );
        })
        .then(() => self.clients.claim()) // Assume controle de todas as abas abertas
        .then(() => {
            // Avisa todas as abas/PWA que há uma nova versão → elas vão recarregar
            return self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        })
        .then((clients) => {
            clients.forEach(client => client.postMessage({ type: 'SW_UPDATED' }));
        })
    );
});

// Estratégia: Network First para tudo
// Para '/' (index.html): SEMPRE da rede, nunca do cache
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // HTML raiz: sempre da rede (nunca cache) — garante atualização do PWA
    if (url.pathname === '/' || url.pathname === '/index.html') {
        event.respondWith(
            fetch(event.request).catch(() => {
                // Só usa cache como último recurso offline
                return caches.match(event.request);
            })
        );
        return;
    }

    // Demais recursos: Network First, fallback cache
    event.respondWith(
        fetch(event.request).catch(() => {
            return caches.match(event.request);
        })
    );
});

// ── WEB PUSH: Campainha com tela apagada / app fechado ────────
self.addEventListener('push', (event) => {
    let payload = { title: '🔔 Alguém no Interfone!', body: 'Toque para ver quem está no portão.' };

    try {
        if (event.data) payload = event.data.json();
    } catch(e) {}

    const options = {
        body: payload.body,
        icon: 'https://cdn-icons-png.flaticon.com/512/1211/1211477.png',
        badge: 'https://cdn-icons-png.flaticon.com/512/1211/1211477.png',
        vibrate: [500, 200, 500, 200, 500, 200, 500, 200, 500],
        tag: 'interfone-ring',
        renotify: true,
        requireInteraction: true,
        actions: [
            { action: 'abrir', title: '📱 Abrir App' },
            { action: 'dispensar', title: '✕ Dispensar' }
        ],
        data: { url: '/' }
    };

    event.waitUntil(self.registration.showNotification(payload.title, options));
});

// ── CLIQUE na notificação ─────────────────────────────────────
self.addEventListener('notificationclick', (event) => {
    event.notification.close();

    if (event.action === 'dispensar') return;

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});
