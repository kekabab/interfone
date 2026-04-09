"""
Servidor do Interfone AI - FastAPI + WebSocket + Socket.IO
- Recebe áudio do ESP32 via WebSocket
- Transcreve com Whisper
- Notifica o App PWA dos moradores via Socket.IO
- Envia respostas de áudio de volta ao ESP32
"""
import asyncio
import os
import time
import os
import asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import socketio
import uvicorn
import time
import struct

# Bypass API key check
OPENAI_API_KEY = "mock_key"
client = None

AUDIO_DIR = Path(__file__).parent
STATIC_DIR = Path(__file__).parent / "static"

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
        self.status = "idle"  # idle, ringing, active, playing_response
        self.current_transcript = ""
        self.ring_start_time: float = 0.0
        self.accumulated_audio = bytearray()
        self.audio_cache: dict[str, bytes] = {}  # Cache de áudios pré-carregados

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
                    await sio.emit("intercom_ring", {"timestamp": state.ring_start_time})

                elif msg == "AUDIO_START":
                    state.accumulated_audio = bytearray()
                    state.status = "active"
                    print("[ESP32] Stream de áudio iniciado...")
                    await sio.emit("intercom_status", {"status": "recording"})

                elif msg == "AUDIO_END":
                    if len(state.accumulated_audio) > 0:
                        print(f"[ESP32] Áudio recebido: {len(state.accumulated_audio)} bytes. Transcrevendo...")
                        await sio.emit("intercom_status", {"status": "transcribing"})
                        text = await transcribe_audio(bytes(state.accumulated_audio))
                        state.current_transcript = text
                        resident = detect_resident(text)
                        await sio.emit("intercom_transcript", {"text": text, "resident": resident})
                        state.status = "waiting_response"
                        await sio.emit("intercom_status", {"status": "waiting_response"})

            elif "bytes" in data:
                state.accumulated_audio.extend(data["bytes"])

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
        return

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
        if state.esp32_ws:
            try:
                start_time = time.time()
                # Enviar comando com sample rate (8000 para respostas rápidas)
                await state.esp32_ws.send_text(f"PLAY_RESPONSE:8000:{audio_file}")
                
                # Pequena pausa para o ESP32 abrir o pipeline de áudio
                await asyncio.sleep(0.2)
                
                # Enviar dados binários do áudio em chunks
                chunk_size = 4096
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    await state.esp32_ws.send_bytes(chunk)
                
                # CÁLCULO DE TEMPO: Áudio PCM 16-bit 8000Hz = 16.000 bytes consumidos por segundo.
                # Se não esperarmos o áudio terminar de tocar, o "PLAY_DONE" chega antes da hora no ESP32 e corta a fala!
                duration = len(audio_data) / 16000.0
                print(f"[AUDIO] Aguardando o buffer tocar no ESP32 por {duration:.2f}s...")
                await asyncio.sleep(duration + 0.5) # Espera a duração do áudio + meio segundo de margem
                
                await state.esp32_ws.send_text("PLAY_DONE")
                elapsed = (time.time() - start_time) * 1000
                print(f"[AUDIO] Playback finalizado em {elapsed:.2f}ms. Total: {len(audio_data)} bytes")
            except Exception as e:
                print(f"[AUDIO] Erro ao enviar: {e}")
    else:
        print(f"[AUDIO] Arquivo não cacheado: {audio_file}")
        # Tentar gerar via TTS como fallback
        print(f"[TTS] Gerando áudio TTS como fallback...")
        if state.esp32_ws:
            await state.esp32_ws.send_text(f"TTS:{resp['text']}")

    state.status = "idle"
    await sio.emit("intercom_status", {"status": "idle", "message": "Pronto"})


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
    print("  PWA: http://<SEU_IP>:8765")
    print("  ESP32 WS: ws://<SEU_IP>:8765/ws/esp32")
    print("=" * 60)
    uvicorn.run(sio_app, host="0.0.0.0", port=8765, log_level="info")
