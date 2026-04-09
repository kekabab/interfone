"""
Servidor do Interfone AI - FastAPI + WebSocket + Socket.IO
- Recebe áudio do ESP32 via WebSocket
- Transcreve com Whisper
- Notifica o App PWA dos moradores via Socket.IO e Web Push
- Envia respostas de áudio de volta ao ESP32
"""
import asyncio
import os
import time
import json
import struct
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import socketio
from openai import AsyncOpenAI

# ── Web Push ───────────────────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_AVAILABLE = True
except ImportError:
    WEBPUSH_AVAILABLE = False
    print("[WARN] pywebpush não instalado. Notificações push desativadas.")

# ── Configuração ──────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

AUDIO_DIR = Path(__file__).parent
STATIC_DIR = Path(__file__).parent / "static"

# ── VAPID Keys (variáveis de ambiente no Render) ───────────────
# Formato esperado: base64url raw (chave curta de ~43 chars, SEM headers PEM)
# Para gerar: python -c "
#   from cryptography.hazmat.primitives.asymmetric import ec
#   import base64
#   k = ec.generate_private_key(ec.SECP256R1())
#   raw = k.private_numbers().private_value.to_bytes(32,'big')
#   print(base64.urlsafe_b64encode(raw).rstrip(b'=').decode())
# "
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_EMAIL       = os.environ.get("VAPID_EMAIL", "mailto:admin@interfone.local")

# Arquivo para persistir subscriptions Push entre deploys
SUBSCRIPTIONS_FILE = Path(__file__).parent / "push_subscriptions.json"

# ── Respostas Rápidas (mapeadas para arquivos de áudio .raw) ──
QUICK_RESPONSES = {
    "descendo": {
        "label": "🏃 Estou descendo!",
        "text": "O morador pediu para aguardar, pois já está descendo.",
        "audio_file": "resp_descendo.raw",
    },
    "ausente": {
        "label": "🚫 Não estou em casa",
        "text": "Lamentamos, mas o morador não se encontra no momento.",
        "audio_file": "resp_ausente.raw",
    }
}

# ── Estado Global ─────────────────────────────────────────────
class IntercomState:
    def __init__(self):
        self.esp32_ws: WebSocket | None = None
        self.status = "idle"  # idle, ringing, active, playing_response, waiting_response
        self.current_transcript = ""
        self.ring_start_time: float = 0.0
        self.accumulated_audio = bytearray()
        self.last_audio_time: float = 0.0
        self.audio_cache: dict[str, bytes] = {}  # Cache de áudios pré-carregados
        self.call_timeout_task: asyncio.Task | None = None  # Task de timeout da chamada
        self.push_subscriptions: list[dict] = self._load_subscriptions()
        self.response_lock = asyncio.Lock()  # Evita double-send simultâneo

    def _load_subscriptions(self) -> list:
        """Carrega subscriptions salvas em disco (sobrevivem a redeploy)."""
        try:
            if SUBSCRIPTIONS_FILE.exists():
                data = json.loads(SUBSCRIPTIONS_FILE.read_text())
                print(f"[PUSH] {len(data)} subscription(s) carregada(s) do disco.")
                return data
        except Exception as e:
            print(f"[PUSH] Erro ao carregar subscriptions: {e}")
        return []

    def _save_subscriptions(self):
        """Salva subscriptions em disco para sobreviver a restarts."""
        try:
            SUBSCRIPTIONS_FILE.write_text(json.dumps(self.push_subscriptions))
        except Exception as e:
            print(f"[PUSH] Erro ao salvar subscriptions: {e}")

    def load_audio_cache(self):
        """Carrega todos os arquivos .raw associados às respostas rápidas."""
        print("[CACHE] Pré-carregando áudios de resposta...")
        for key, resp in QUICK_RESPONSES.items():
            audio_path = AUDIO_DIR / resp["audio_file"]
            if audio_path.exists():
                self.audio_cache[resp["audio_file"]] = audio_path.read_bytes()
                print(f"  - {resp['audio_file']} ({len(self.audio_cache[resp['audio_file']])} bytes)")
            else:
                print(f"  - [!] Erro: {resp['audio_file']} não encontrado em {AUDIO_DIR}")

state = IntercomState()
state.load_audio_cache()

# ── Socket.IO (para o App PWA dos moradores) ──────────────────
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=60,    # 60 segundos de tolerância
    ping_interval=25    # Pings a cada 25 segundos
)

# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(title="Interfone AI")

# Montar Socket.IO como sub-aplicação ASGI
sio_app = socketio.ASGIApp(sio, other_asgi_app=app)

# Servir arquivos estáticos (PWA)
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Rotas HTTP ────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(STATIC_DIR / "manifest.json"))


@app.get("/sw.js")
async def service_worker():
    return FileResponse(str(STATIC_DIR / "sw.js"), media_type="application/javascript")


@app.get("/api/status")
async def api_status():
    return {
        "status": state.status,
        "transcript": state.current_transcript,
        "responses": {k: v["label"] for k, v in QUICK_RESPONSES.items()},
    }


@app.get("/api/responses")
async def api_responses():
    return {k: {"label": v["label"], "text": v["text"]} for k, v in QUICK_RESPONSES.items()}


@app.get("/api/vapid-public-key")
async def vapid_public_key():
    """Retorna a chave pública VAPID para o frontend subscrever ao Push."""
    return JSONResponse({"publicKey": VAPID_PUBLIC_KEY})


@app.post("/api/subscribe")
async def push_subscribe(request: Request):
    """Recebe e salva a subscription Push do browser do morador."""
    try:
        sub = await request.json()
        endpoint = sub.get("endpoint", "")
        state.push_subscriptions = [s for s in state.push_subscriptions if s.get("endpoint") != endpoint]
        state.push_subscriptions.append(sub)
        state._save_subscriptions()
        print(f"[PUSH] Nova subscription salva. Total: {len(state.push_subscriptions)}")
        return JSONResponse({"ok": True, "total": len(state.push_subscriptions)})
    except Exception as e:
        print(f"[PUSH] Erro ao salvar subscription: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.delete("/api/subscribe")
async def push_unsubscribe(request: Request):
    """Remove a subscription Push do morador."""
    try:
        body = await request.json()
        endpoint = body.get("endpoint", "")
        before = len(state.push_subscriptions)
        state.push_subscriptions = [s for s in state.push_subscriptions if s.get("endpoint") != endpoint]
        state._save_subscriptions()
        print(f"[PUSH] Subscription removida. {before} → {len(state.push_subscriptions)}")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/test-push")
async def test_push():
    """Dispara um push de teste para todas as subscriptions ativas. Útil para diagnóstico."""
    if not state.push_subscriptions:
        return JSONResponse({"ok": False, "error": "Nenhuma subscription ativa. Ative o push no PWA primeiro."})
    await send_push_notifications("🔔 Teste do Interfone!", "Push funcionando! Tela apagada OK.")
    return JSONResponse({"ok": True, "total_subs": len(state.push_subscriptions)})


@app.delete("/api/subscribe")
async def push_unsubscribe(request: Request):
    """Remove a subscription Push do morador."""
    try:
        body = await request.json()
        endpoint = body.get("endpoint", "")
        before = len(state.push_subscriptions)
        state.push_subscriptions = [s for s in state.push_subscriptions if s.get("endpoint") != endpoint]
        print(f"[PUSH] Subscription removida. {before} → {len(state.push_subscriptions)}")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Web Push Helper ───────────────────────────────────────────
async def send_push_notifications(title: str, body: str):
    """Envia notificação Push para todos os moradores, mesmo com app fechado."""
    if not WEBPUSH_AVAILABLE or not VAPID_PRIVATE_KEY:
        print("[PUSH] Skipping push: pywebpush não disponível ou VAPID_PRIVATE_KEY não configurada.")
        return

    if not state.push_subscriptions:
        print("[PUSH] Sem subscriptions ativas.")
        return

    print(f"[PUSH] Enviando para {len(state.push_subscriptions)} subscription(s)...")
    payload = json.dumps({"title": title, "body": body})
    dead_subscriptions = []

    for sub in state.push_subscriptions:
        try:
            # VAPID_PRIVATE_KEY deve ser base64url raw (ex: 'MAm8o2A0eh...')
            # NÃO PEM — formato mais confiável com pywebpush
            await asyncio.to_thread(
                webpush,
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_EMAIL},
                content_encoding="aes128gcm",
            )
            print(f"[PUSH] ✓ Notificação enviada para {sub.get('endpoint', 'unknown')[:50]}...")
        except WebPushException as ex:
            print(f"[PUSH] ✗ Erro ao enviar push: {ex}")
            # Se a subscription expirou (410 Gone), remove
            if ex.response and ex.response.status_code in (404, 410):
                dead_subscriptions.append(sub.get("endpoint"))
        except Exception as ex:
            print(f"[PUSH] ✗ Erro inesperado: {ex}")

    # Limpar subscriptions mortas
    if dead_subscriptions:
        state.push_subscriptions = [s for s in state.push_subscriptions if s.get("endpoint") not in dead_subscriptions]
        state._save_subscriptions()
        print(f"[PUSH] {len(dead_subscriptions)} subscription(s) expirada(s) removida(s).")


# ── Whisper Transcription ─────────────────────────────────────
async def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcreve áudio PCM 16-bit 8kHz mono usando Whisper."""
    print(f"[WHISPER] Transcrevendo {len(audio_bytes)} bytes de áudio...")
    wav_path = str(AUDIO_DIR / "visitor_temp.wav")

    # Criar cabeçalho WAV
    num_channels = 1
    sample_rate = 8000
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(audio_bytes)

    with open(wav_path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + data_size))
        f.write(b"WAVEfmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(audio_bytes)

    try:
        with open(wav_path, "rb") as audio_file:
            transcription = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="pt",
                prompt="Nomes corretos: Maurício, Cláudia, Lígia, Paloma. (Interfone da casa 497).",
            )
        text = transcription.text
        print(f"[WHISPER] Transcrição: '{text}'")
        return text
    except Exception as e:
        print(f"[WHISPER] Erro: {e}")
        return ""


# ── Detecção de Morador ───────────────────────────────────────
def detect_resident(text: str) -> str:
    """Detecta qual morador foi mencionado no texto."""
    t = text.lower()
    if "maur" in t or "rício" in t:
        return "mauricio"
    elif "cláud" in t or "claud" in t:
        return "claudia"
    elif "líg" in t or "lig" in t:
        return "ligia"
    elif "palom" in t:
        return "paloma"
    return "todos"


async def call_timeout(seconds: int = 40):
    """Encerra a sessão de atendimento automaticamente depois de N segundos."""
    await asyncio.sleep(seconds)
    if state.status in ("waiting_response", "playing_response", "ringing", "active"):
        print(f"[TIMEOUT] Sessão encerrada após {seconds}s")
        state.status = "idle"
        await sio.emit("intercom_status", {"status": "idle", "message": "Sessão encerrada"})


# ── WebSocket do ESP32 ────────────────────────────────────────
@app.websocket("/ws/esp32")
async def esp32_websocket(websocket: WebSocket):
    await websocket.accept()
    state.esp32_ws = websocket
    print("\n[+] ESP32 Interfone CONECTADO!")

    try:
        while True:
            data = await websocket.receive()
            if "text" in data:
                msg = data["text"]
                print(f"[ESP32] Texto recebido: {msg}")

                if msg == "BELL_PRESSED":
                    state.status = "visitor_arrived"
                    print("\n🔔 Campainha detectada!")
                    await sio.emit("intercom_status", {"status": "visitor_arrived", "message": "Alguém no portão"})

                elif msg == "TRIGGER_CALL":
                    state.status = "ringing"
                    state.ring_start_time = time.time()
                    print("\n☎️ DISPARANDO CHAMADA!")
                    # Cancela timeout anterior se existir
                    if state.call_timeout_task and not state.call_timeout_task.done():
                        state.call_timeout_task.cancel()
                    # Inicia timeout de 40 segundos para esta sessão
                    state.call_timeout_task = asyncio.create_task(call_timeout(40))

                    # 1. Notifica via Socket.IO (app aberto ou em background recente)
                    await sio.emit("intercom_ring", {"timestamp": state.ring_start_time})

                    # 2. Notifica via Web Push (app fechado, tela apagada)
                    asyncio.create_task(send_push_notifications(
                        "🔔 Alguém no Interfone!",
                        "Toque para ver quem está no portão."
                    ))

                elif msg == "AUDIO_START":
                    state.accumulated_audio = bytearray()
                    state.status = "active"
                    print("[ESP32] Stream de áudio iniciado...")
                    await sio.emit("intercom_status", {"status": "recording"})

                elif msg == "AUDIO_END":
                    if len(state.accumulated_audio) > 0:
                        print(f"[ESP32] Áudio recebido: {len(state.accumulated_audio)} bytes. Transcrevendo...")
                        await sio.emit("intercom_status", {"status": "transcribing", "message": "🧠 IA Processando fala..."})
                        try:
                            text = await transcribe_audio(bytes(state.accumulated_audio))
                            state.current_transcript = text
                            if text:
                                resident = detect_resident(text)
                                await sio.emit("intercom_transcript", {"text": text, "resident": resident})
                            else:
                                print("[WHISPER] Transcrição retornou vazia.")
                                await sio.emit("intercom_transcript", {"text": "", "message": "Nenhuma fala detectada"})
                        except Exception as e:
                            print(f"[ERROR] Falha na transcrição: {e}")
                            await sio.emit("intercom_transcript", {"text": "", "message": "Erro na transcrição"})

                        state.status = "waiting_response"
                        await sio.emit("intercom_status", {"status": "waiting_response", "message": "Aguardando sua resposta..."})

            elif "bytes" in data:
                state.accumulated_audio.extend(data["bytes"])
                state.last_audio_time = time.time()

                # Se acumulamos muito áudio (ex: > 15s), podemos ter perdido o AUDIO_END
                # 8000Hz * 2 bytes * 15s = 240.000 bytes
                if len(state.accumulated_audio) > 240000:
                   print("[DEBUG] Buffer de áudio muito grande, forçando transcrição...")

    except WebSocketDisconnect:
        print("[-] ESP32 desconectado normalmente.")
    except Exception as e:
        print(f"[!] Erro crítico no WebSocket do ESP32: {e}")
    finally:
        state.esp32_ws = None
        state.status = "idle"
        await sio.emit("intercom_status", {"status": "offline", "esp32_online": False})


# ── Socket.IO Events (App dos Moradores) ──────────────────────
@sio.event
async def connect(sid, environ):
    print(f"[APP] Morador conectado: {sid}")
    await sio.emit("intercom_status", {
        "status": state.status,
        "message": "Conectado ao interfone",
        "esp32_online": state.esp32_ws is not None,
    }, to=sid)


@sio.event
async def disconnect(sid):
    print(f"[APP] Morador desconectado: {sid}")


@sio.event
async def quick_response(sid, data):
    """Morador clicou em um botão de resposta rápida."""
    response_key = data.get("response", "")
    print(f"\n[APP] Morador {sid} respondeu: {response_key}")

    if response_key not in QUICK_RESPONSES:
        print(f"[APP] Resposta desconhecida: {response_key}")
        await sio.emit("response_ack", {"ok": False, "error": "Resposta desconhecida"}, to=sid)
        return

    # Verificar se o ESP32 está conectado ANTES de tentar enviar
    if not state.esp32_ws:
        print("[APP] ESP32 desconectado! Não é possível enviar resposta.")
        await sio.emit("response_ack", {
            "ok": False,
            "error": "Interfone offline. Reconecte o ESP32."
        }, to=sid)
        return

    # Lock para evitar que dois moradores enviem resposta simultaneamente
    if state.response_lock.locked():
        print("[APP] Outro áudio já está sendo enviado. Aguardando...")
        await sio.emit("response_ack", {
            "ok": False,
            "error": "Aguarde, outro áudio está sendo reproduzido."
        }, to=sid)
        return

    async with state.response_lock:
        resp = QUICK_RESPONSES[response_key]
        state.status = "playing_response"

        await sio.emit("intercom_status", {
            "status": "playing_response",
            "message": f"Tocando: {resp['label']}",
        })

        # Enviar áudio de resposta para o ESP32
        audio_file = resp["audio_file"]
        if audio_file in state.audio_cache:
            audio_data = state.audio_cache[audio_file]
            print(f"[AUDIO] Enviando {audio_file} (da memória) para o ESP32...")
            try:
                start_time = time.time()

                # Pequeno delay para garantir que pipeline anterior foi finalizado
                await asyncio.sleep(0.2)

                # Enviar comando com sample rate (8000 para respostas rápidas)
                await state.esp32_ws.send_text(f"PLAY_RESPONSE:8000:{audio_file}")

                # Pausa para o ESP32 abrir o pipeline com segurança
                await asyncio.sleep(0.4)

                # Enviar 400ms de silêncio para dar tempo de o DAC/Amplificador desmutar
                # 8000 samples/sec * 2 bytes/sample * 0.4s = 6400 bytes
                await state.esp32_ws.send_bytes(bytes(6400))

                # Enviar dados binários do áudio em chunks
                chunk_size = 4096
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    await state.esp32_ws.send_bytes(chunk)

                # Esperar duração do áudio + margem
                # PCM 16-bit 8000Hz = 16.000 bytes por segundo
                duration = len(audio_data) / 16000.0
                print(f"[AUDIO] Aguardando o buffer tocar no ESP32 por {duration:.2f}s...")
                await asyncio.sleep(duration + 0.5)

                await state.esp32_ws.send_text("PLAY_DONE")
                elapsed = (time.time() - start_time) * 1000
                print(f"[AUDIO] Playback finalizado em {elapsed:.2f}ms. Total: {len(audio_data)} bytes")

                # ✅ Confirmar sucesso para o PWA
                await sio.emit("response_ack", {"ok": True, "label": resp["label"]}, to=sid)

            except Exception as e:
                print(f"[AUDIO] Erro ao enviar: {e}")
                await sio.emit("response_ack", {
                    "ok": False,
                    "error": f"Erro ao enviar áudio: {str(e)}"
                }, to=sid)
        else:
            print(f"[AUDIO] Arquivo não cacheado: {audio_file}")
            # Tentar gerar via TTS como fallback
            if state.esp32_ws:
                await state.esp32_ws.send_text(f"TTS:{resp['text']}")
            await sio.emit("response_ack", {"ok": True, "label": resp["label"] + " (TTS)"}, to=sid)

    state.status = "waiting_response"
    await sio.emit("intercom_status", {"status": "waiting_response", "message": "Resposta enviada! Pode enviar outra."})


@sio.event
async def dismiss_call(sid, data):
    """Morador ignorou/dispensou a chamada."""
    print(f"[APP] Morador {sid} dispensou a chamada.")
    state.status = "idle"
    await sio.emit("intercom_status", {"status": "idle", "message": "Chamada dispensada"})


# ── Ponto de Entrada ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  INTERFONE AI - Servidor Local")
    print(f"  PWA: http://<SEU_IP>:8765")
    print(f"  ESP32 WS: ws://<SEU_IP>:8765/ws/esp32")
    print("=" * 60)
    uvicorn.run(sio_app, host="0.0.0.0", port=8765, log_level="info")
