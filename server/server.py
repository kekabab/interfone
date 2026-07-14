"""
Servidor do Interfone AI - FastAPI + WebSocket + Socket.IO
- Ponte bidirecional entre o ESP32 e a Gemini Live API (atendimento em tempo real)
- Recebe áudio do visitante (PCM 16kHz) e repassa ao Gemini Live
- Recebe áudio falado da IA (PCM 24kHz) e envia ao ESP32 para tocar
- Notifica o App PWA dos moradores via Socket.IO e Web Push (transcrição ao vivo)
- Botões de resposta rápida do morador interrompem a IA e injetam áudio .raw
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
from google import genai
from google.genai import types

# ── Web Push ───────────────────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_AVAILABLE = True
except ImportError:
    WEBPUSH_AVAILABLE = False
    print("[WARN] pywebpush não instalado. Notificações push desativadas.")

# ── Configuração ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-live-preview")

# Cliente Gemini reutilizável (a sessão live é aberta por campainha)
genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
if not genai_client:
    print("[FATAL] GEMINI_API_KEY não configurada! A IA não vai funcionar.")

AUDIO_DIR = Path(__file__).parent
STATIC_DIR = Path(__file__).parent / "static"

# ── VAPID Keys (variáveis de ambiente no Render) ───────────────
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_EMAIL       = os.environ.get("VAPID_EMAIL", "mailto:admin@interfone.local")

# Arquivo para persistir subscriptions Push entre deploys
SUBSCRIPTIONS_FILE = Path(__file__).parent / "push_subscriptions.json"

# ── Persona da IA (Recepcionista de portaria 497) ─────────────
SYSTEM_INSTRUCTION = (
    "Você é a recepcionista eletrônica do portão da residência 497. "
    "Atenda o visitante com educação e formalidade, em português do Brasil, de forma concisa. "
    "Pergunte com quem o visitante deseja falar e o motivo da visita. "
    "Os moradores são: Maurício, Cláudia, Lígia e Paloma. "
    "Anuncie que vai avisar o morador e mantenha o visitante informado de forma breve. "
    "Não invente informações sobre os moradores ou o horário deles. "
    "Se o morador não atender, informe com educação e sugira que deixe um recado ou volte mais tarde. "
    "Suas respostas faladas devem ser curtas e naturais, como uma conversa de interfone — "
    "no máximo uma ou duas frases por vez."
)

# ── Respostas Rápidas (mapeadas para arquivos de áudio .raw) ──
# Nota: os .raw de resposta rápida foram gerados a 8000Hz (ElevenLabs -> ffmpeg -ar 8000).
# O ESP32 toca no rate que o servidor informar no comando PLAY_RESPONSE:<rate>:.
QUICK_RESPONSES = {
    "descendo": {
        "label": "🏃 Estou descendo!",
        "text": "O morador pediu para aguardar, pois já está descendo.",
        "audio_file": "resp_descendo.raw",
        "audio_rate": 8000,
    },
    "ausente": {
        "label": "🚫 Não estou em casa",
        "text": "Lamentamos, mas o morador não se encontra no momento.",
        "audio_file": "resp_ausente.raw",
        "audio_rate": 8000,
    }
}


# ── Sessão Gemini Live ────────────────────────────────────────
class GeminiLiveSession:
    """Encapsula uma sessão da Gemini Live API ligada a um ESP32 conectado.

    - Recebe chunks de áudio do visitante (PCM 16kHz) e envia ao Gemini.
    - Recebe áudio falado da IA (PCM 24kHz) e envia ao ESP32 para tocar.
    - Emite transcrições (entrada do visitante / saída da IA) pro PWA via Socket.IO.
    - Mic ducking: enquanto a IA está falando (enviando áudio), o áudio do mic do
      ESP32 NÃO é repassado ao Gemini — evita que a IA se ouça (eco acústico).
    """

    def __init__(self, esp32_ws: WebSocket, sio_ref, on_close=None):
        self.esp32_ws = esp32_ws
        self.sio = sio_ref
        self.on_close = on_close
        self.session = None  # AsyncSession do Gemini
        self._cm = None  # mantém referência ao context manager pra evitar GC prematuro
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._mic_muted = False  # ducking: True = não repassar mic ao Gemini
        self._outgoing_audio = asyncio.Lock()

    async def start(self):
        """Abre a sessão Gemini Live e dispara o loop de recebimento."""
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_INSTRUCTION,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                ),
            ),
        )
        # live.connect retorna um AsyncSession quando usado com __aenter__.
        # IMPORTANTE: guardamos a referência do context manager (self._cm) para evitar
        # que o garbage collector dispare __aexit__ e feche a sessão prematuramente.
        self._cm = genai_client.aio.live.connect(model=GEMINI_MODEL, config=config)
        self.session = await self._cm.__aenter__()
        print("[GEMINI] Sessão live aberta.")
        self._recv_task = asyncio.create_task(self._receive_loop())
        return self

    async def feed_visitor_audio(self, pcm16k: bytes):
        """Repassa áudio do visitante (PCM 16kHz) ao Gemini, exceto em ducking."""
        if self._closed or not self.session:
            return
        if self._mic_muted:
            return  # IA está falando: ignora o mic para evitar eco
        try:
            await self.session.send_realtime_input(
                audio=types.Blob(data=pcm16k, mime_type="audio/pcm;rate=16000")
            )
        except Exception as e:
            print(f"[GEMINI] Erro ao enviar áudio do visitante: {e}")

    async def inject_quick_response(self, text_for_ia: str):
        """Morador apertou um botão: pede à IA que encerre em uma frase curta.

        O áudio .raw correspondente é enviado ao ESP32 pelo chamador (quick_response handler).
        Aqui apenas orientamos a IA a não competir com a frase gravada.
        """
        if self._closed or not self.session:
            return
        try:
            await self.session.send_realtime_input(text=text_for_ia)
        except Exception as e:
            print(f"[GEMINI] Erro ao injetar resposta rápida: {e}")

    async def _receive_loop(self):
        """Loop que lê as mensagens do Gemini e repassa áudio + transcrição."""
        try:
            async for message in self.session.receive():
                if self._closed:
                    break
                sc = getattr(message, "server_content", None)
                if not sc:
                    continue

                # 1) Áudio falado pela IA -> enviar ao ESP32
                model_turn = getattr(sc, "model_turn", None)
                if model_turn and getattr(model_turn, "parts", None):
                    for part in model_turn.parts:
                        blob = getattr(part, "inline", None) or getattr(part, "inline_data", None)
                        if blob and getattr(blob, "data", None):
                            self._mic_muted = True
                            await self._send_audio_to_esp32(blob.data)
                # Ducking: enquanto a IA fala, emudece o mic; ao parar, libera
                if self._mic_muted:
                    self._mic_muted = True
                else:
                    self._mic_muted = False

                # 2) Transcrição da SAÍDA da IA (o que ela disse)
                ot = getattr(sc, "output_transcription", None)
                if ot and getattr(ot, "text", None):
                    await self.sio.emit("ia_transcript", {"text": ot.text, "who": "ia"})

                # 3) Transcrição da ENTRADA do visitante (o que ele disse)
                it = getattr(sc, "input_transcription", None)
                if it and getattr(it, "text", None):
                    await self.sio.emit("intercom_transcript", {"text": it.text, "resident": "todos"})

                # 4) Turno completo
                if getattr(sc, "turn_complete", False):
                    self._mic_muted = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[GEMINI] Erro no loop de recebimento: {e}")
        finally:
            print("[GEMINI] Loop de recebimento encerrado.")

    async def _send_audio_to_esp32(self, pcm24k: bytes):
        """Envia um chunk de áudio da IA (PCM 24kHz) ao ESP32 para tocar."""
        if self._closed or not self.esp32_ws:
            return
        async with self._outgoing_audio:
            try:
                # Chunks pequenos para baixa latência
                chunk_size = 4096
                for i in range(0, len(pcm24k), chunk_size):
                    chunk = pcm24k[i:i + chunk_size]
                    await self.esp32_ws.send_bytes(chunk)
            except Exception as e:
                print(f"[ESP32] Erro ao enviar áudio da IA: {e}")

    async def close(self):
        """Encerra a sessão Gemini e notifica o ESP32."""
        if self._closed:
            return
        self._closed = True
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self.session:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None
        if self._cm:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None
        print("[GEMINI] Sessão live fechada.")


# ── Estado Global ─────────────────────────────────────────────
class IntercomState:
    def __init__(self):
        self.esp32_ws: WebSocket | None = None
        self.status = "idle"  # idle, ringing, live, playing_response, waiting_response
        self.current_transcript = ""
        self.ring_start_time: float = 0.0
        self.audio_cache: dict[str, bytes] = {}  # Cache de áudios .raw
        self.call_timeout_task: asyncio.Task | None = None
        self.live_session: GeminiLiveSession | None = None  # sessão Gemini ativa
        self.push_subscriptions: list[dict] = self._load_subscriptions()
        self.response_lock = asyncio.Lock()  # Evita double-send simultâneo

    def _load_subscriptions(self) -> list:
        try:
            if SUBSCRIPTIONS_FILE.exists():
                data = json.loads(SUBSCRIPTIONS_FILE.read_text())
                print(f"[PUSH] {len(data)} subscription(s) carregada(s) do disco.")
                return data
        except Exception as e:
            print(f"[PUSH] Erro ao carregar subscriptions: {e}")
        return []

    def _save_subscriptions(self):
        try:
            SUBSCRIPTIONS_FILE.write_text(json.dumps(self.push_subscriptions))
        except Exception as e:
            print(f"[PUSH] Erro ao salvar subscriptions: {e}")

    def load_audio_cache(self):
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
    ping_timeout=60,
    ping_interval=25
)

# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(title="Interfone AI")
sio_app = socketio.ASGIApp(sio, other_asgi_app=app)
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
    return JSONResponse({"publicKey": VAPID_PUBLIC_KEY})

@app.post("/api/subscribe")
async def push_subscribe(request: Request):
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
    if not state.push_subscriptions:
        return JSONResponse({"ok": False, "error": "Nenhuma subscription ativa."})
    await send_push_notifications("🔔 Teste do Interfone!", "Push funcionando!")
    return JSONResponse({"ok": True, "total_subs": len(state.push_subscriptions)})


@app.post("/api/test-ia")
async def test_ia():
    """Força a IA a falar uma frase de teste (diagnóstico do caminho de áudio).

    Requer que uma sessão live esteja ativa (ESP32 conectado e TRIGGER_CALL enviado).
    Útil para validar que o áudio da IA chega ao ESP32 sem precisar de fala real.
    """
    if not state.live_session or state.live_session._closed:
        return JSONResponse({"ok": False, "error": "Nenhuma sessão live ativa. Toque a campainha primeiro."})
    try:
        await state.live_session.inject_quick_response(
            "Por favor, diga em voz alta: 'Teste de áudio do interfone, funcionando perfeitamente.'"
        )
        return JSONResponse({"ok": True, "message": "Frase de teste enviada à IA. Ouça o alto-falante do interfone."})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Web Push Helper ───────────────────────────────────────────
async def send_push_notifications(title: str, body: str):
    if not WEBPUSH_AVAILABLE or not VAPID_PRIVATE_KEY:
        print("[PUSH] Skipping push: pywebpush indisponível ou VAPID não configurado.")
        return
    if not state.push_subscriptions:
        print("[PUSH] Sem subscriptions ativas.")
        return

    print(f"[PUSH] Enviando para {len(state.push_subscriptions)} subscription(s)...")
    payload = json.dumps({"title": title, "body": body})
    dead_subscriptions = []

    for sub in state.push_subscriptions:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_EMAIL},
                content_encoding="aes128gcm",
            )
            print(f"[PUSH] ✓ Enviado para {sub.get('endpoint', 'unknown')[:50]}...")
        except WebPushException as ex:
            print(f"[PUSH] ✗ Erro: {ex}")
            if ex.response and ex.response.status_code in (404, 410):
                dead_subscriptions.append(sub.get("endpoint"))
        except Exception as ex:
            print(f"[PUSH] ✗ Erro inesperado: {ex}")

    if dead_subscriptions:
        state.push_subscriptions = [s for s in state.push_subscriptions if s.get("endpoint") not in dead_subscriptions]
        state._save_subscriptions()
        print(f"[PUSH] {len(dead_subscriptions)} subscription(s) expirada(s) removida(s).")


# ── Timeout de sessão ────────────────────────────────────────
async def call_timeout(seconds: int = 120):
    """Encerra a sessão de atendimento automaticamente depois de N segundos."""
    await asyncio.sleep(seconds)
    if state.status in ("live", "playing_response", "ringing", "waiting_response"):
        print(f"[TIMEOUT] Sessão encerrada após {seconds}s")
        await end_live_session()
        state.status = "idle"
        await sio.emit("intercom_status", {"status": "idle", "message": "Sessão encerrada por tempo"})


async def end_live_session():
    """Close Gemini and return the ESP32 to an idle state."""
    current_task = asyncio.current_task()
    if state.call_timeout_task and state.call_timeout_task is not current_task:
        if not state.call_timeout_task.done():
            state.call_timeout_task.cancel()
        state.call_timeout_task = None

    # END_SESSION is required to make the ESP32 accept the next doorbell press.
    if state.esp32_ws:
        try:
            await state.esp32_ws.send_text("END_SESSION")
        except Exception:
            pass

    if state.live_session:
        await state.live_session.close()
        state.live_session = None


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
                    if state.status != "idle":
                        print(f"[ESP32] TRIGGER_CALL ignored: {state.status} session already active.")
                        continue

                    state.ring_start_time = time.time()
                    print("[CALL] Starting Gemini Live session.")
                    state.status = "connecting"
                    await sio.emit("intercom_status", {"status": "connecting", "message": "Conectando a IA..."})

                    if not genai_client:
                        print("[GEMINI] Missing API key.")
                        await websocket.send_text("END_SESSION")
                        state.status = "idle"
                        await sio.emit("intercom_status", {"status": "error", "message": "IA nao configurada."})
                        continue

                    try:
                        state.live_session = await GeminiLiveSession(websocket, sio).start()
                        await websocket.send_text("PLAY_LIVE_START:24000")
                        await asyncio.sleep(0.2)
                        await state.live_session.inject_quick_response(
                            "The doorbell just rang. Greet the visitor now in Brazilian Portuguese and ask who they want to speak with."
                        )
                        state.status = "live"
                        state.call_timeout_task = asyncio.create_task(call_timeout(120))
                        await sio.emit("intercom_status", {"status": "live", "message": "IA atendendo..."})
                        await sio.emit("intercom_ring", {"timestamp": state.ring_start_time, "ia": True})
                        asyncio.create_task(send_push_notifications(
                            "Interfone", "Alguem esta no portao. A IA esta atendendo."
                        ))
                    except Exception as e:
                        print(f"[GEMINI] Failed to start session: {e}")
                        await end_live_session()
                        state.status = "idle"
                        await sio.emit("intercom_status", {"status": "error", "message": "Nao foi possivel iniciar a IA."})

                elif msg == "AUDIO_START":
                    # Legacy: no modo live, o áudio já flui em chunks binários.
                    print("[ESP32] AUDIO_START (legacy).")

                elif msg == "AUDIO_END":
                    # Legacy: no modo live não acumulamos mais.
                    print("[ESP32] AUDIO_END (legacy).")

                elif msg == "END_CALL":
                    # O ESP32 encerrou a chamada (ex: visitante foi embora)
                    print("[ESP32] Chamada encerrada pelo ESP32.")
                    await end_live_session()
                    state.status = "idle"
                    await sio.emit("intercom_status", {"status": "idle", "message": "Chamada encerrada"})

            elif "bytes" in data:
                # Chunk de áudio do visitante (PCM 16kHz) -> repassa ao Gemini Live
                if state.live_session and not state.live_session._closed:
                    await state.live_session.feed_visitor_audio(data["bytes"])

    except WebSocketDisconnect:
        print("[-] ESP32 desconectado normalmente.")
    except Exception as e:
        print(f"[!] Erro crítico no WebSocket do ESP32: {e}")
    finally:
        state.esp32_ws = None
        await end_live_session()
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
    """Morador clicou em um botão de resposta rápida.

    1. Pede à IA que encerre a conversa com uma frase curta.
    2. Interrompe o streaming de áudio da IA (corta a fala atual).
    3. Envia o áudio .raw pré-gravado ao ESP32 (taxa do arquivo).
    4. Aguarda o playback terminar.
    5. Encerra a sessão Gemini.
    """
    response_key = data.get("response", "")
    print(f"\n[APP] Morador {sid} respondeu: {response_key}")

    if response_key not in QUICK_RESPONSES:
        await sio.emit("response_ack", {"ok": False, "error": "Resposta desconhecida"}, to=sid)
        return

    if not state.esp32_ws:
        await sio.emit("response_ack", {"ok": False, "error": "Interfone offline."}, to=sid)
        return

    if state.status != "live" or not state.live_session:
        await sio.emit("response_ack", {"ok": False, "error": "A chamada ainda esta conectando."}, to=sid)
        return

    if state.response_lock.locked():
        await sio.emit("response_ack", {"ok": False, "error": "Aguarde, outro áudio está sendo reproduzido."}, to=sid)
        return

    async with state.response_lock:
        resp = QUICK_RESPONSES[response_key]
        state.status = "playing_response"
        await sio.emit("intercom_status", {
            "status": "playing_response",
            "message": f"Tocando: {resp['label']}",
        })

        # 1. Interrompe a IA: sinaliza ao ESP32 para PARAR o player contínuo
        if state.live_session:
            try:
                await state.esp32_ws.send_text("PLAY_LIVE_END")
            except Exception:
                pass
            # A resposta do morador já é reproduzida como PCM pré-gravado abaixo.
            # Não pedimos uma nova fala ao Gemini aqui: áudio Live concorrente
            # poderia chegar no WebSocket durante o PCM e corromper a reprodução.

        # 2. Envia o áudio .raw pré-gravado ao ESP32
        audio_file = resp["audio_file"]
        audio_rate = resp["audio_rate"]
        if audio_file in state.audio_cache:
            audio_data = state.audio_cache[audio_file]
            print(f"[AUDIO] Enviando {audio_file} (rate={audio_rate}) para o ESP32...")
            try:
                start_time = time.time()
                await asyncio.sleep(0.2)  # garante que PLAY_LIVE_END foi processado
                await state.esp32_ws.send_text(f"PLAY_RESPONSE:{audio_rate}:{audio_file}")
                await asyncio.sleep(0.4)

                # 400ms de silêncio para acordar o DAC/amplificador
                silence_len = int(audio_rate * 2 * 0.4)
                await state.esp32_ws.send_bytes(bytes(silence_len))

                # Dados do áudio em chunks
                chunk_size = 4096
                for i in range(0, len(audio_data), chunk_size):
                    await state.esp32_ws.send_bytes(audio_data[i:i + chunk_size])

                # Aguarda a duração do áudio tocar
                duration = len(audio_data) / (audio_rate * 2.0)
                print(f"[AUDIO] Aguardando playback de {duration:.2f}s...")
                await asyncio.sleep(duration + 0.5)
                await state.esp32_ws.send_text("PLAY_DONE")
                elapsed = (time.time() - start_time) * 1000
                print(f"[AUDIO] Playback finalizado em {elapsed:.2f}ms.")
                await sio.emit("response_ack", {"ok": True, "label": resp["label"]}, to=sid)
            except Exception as e:
                print(f"[AUDIO] Erro ao enviar: {e}")
                await sio.emit("response_ack", {"ok": False, "error": f"Erro ao enviar áudio: {e}"}, to=sid)
        else:
            print(f"[AUDIO] Arquivo não cacheado: {audio_file}")
            await sio.emit("response_ack", {"ok": False, "error": "Áudio não encontrado"}, to=sid)

        # 3. Encerra a sessão Gemini (o morador já tomou uma decisão)
        await end_live_session()
        state.status = "idle"
        await sio.emit("intercom_status", {"status": "idle", "message": "Resposta enviada!"})


@sio.event
async def dismiss_call(sid, data):
    """Morador ignorou/dispensou a chamada."""
    print(f"[APP] Morador {sid} dispensou a chamada.")
    await end_live_session()
    state.status = "idle"
    await sio.emit("intercom_status", {"status": "idle", "message": "Chamada dispensada"})


# ── Ponto de Entrada ──────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("=" * 60)
    print("  INTERFONE AI (Gemini Live) - Servidor Local")
    print(f"  PWA: http://<SEU_IP>:8765")
    print(f"  ESP32 WS: ws://<SEU_IP>:8765/ws/esp32")
    print(f"  Modelo Gemini: {GEMINI_MODEL}")
    print("=" * 60)
    uvicorn.run(sio_app, host="0.0.0.0", port=8765, log_level="info")
