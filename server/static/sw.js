const CACHE_NAME = 'interfone-ai-v4';
const ASSETS = [
    '/',
    '/manifest.json'
];

self.addEventListener('install', (event) => {
    self.skipWaiting(); // Força a atualização imediata
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS);
        })
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName); // Apaga versão velha
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Estratégia: Network First
self.addEventListener('fetch', (event) => {
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
        // Vibração intensa estilo chamada telefônica (500ms on, 200ms off, repetido)
        vibrate: [500, 200, 500, 200, 500, 200, 500, 200, 500],
        tag: 'interfone-ring',    // Garante que só 1 notificação aparece (substituição)
        renotify: true,           // Vibra mesmo se já existia uma com o mesmo tag
        requireInteraction: true, // Mantém a notificação na tela até o usuário interagir
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
    
    if (event.action === 'dispensar') {
        // Só fecha a notificação, não abre o app
        return;
    }

    // Ação 'abrir' ou clique direto: foca ou abre o app
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            // Se o app já está aberto em alguma guia, foca nela
            for (const client of clientList) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            // Senão, abre uma nova janela
            if (clients.openWindow) {
                return clients.openWindow('/');
            }
        })
    );
});
