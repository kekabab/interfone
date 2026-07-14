"""
Smoke test da Gemini Live API.
Valida: autenticação, modelo, abrir sessão, receber transcrição, receber áudio.
NÃO usa o server.py — é isolado.
"""
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import struct
import math
from google import genai
from google.genai import types

API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Candidatos a modelo (a Live API tem mudado de nome ao longo do tempo)
MODEL_CANDIDATES = [
    "gemini-3.1-flash-live-preview",
    "gemini-2.5-flash-native-audio-preview-12-2025",
]


SYSTEM_INSTRUCTION = (
    "Você é a recepcionista eletrônica do portão da residência 497. "
    "Atenda o visitante com educação e formalidade, em português do Brasil, de forma concisa. "
    "Pergunte com quem o visitante deseja falar e o motivo. "
    "Os moradores são: Maurício, Cláudia, Lígia e Paloma. "
    "Suas respostas faladas devem ser curtas e naturais, como uma conversa de interfone."
)


def make_silence_pcm(duration_sec: float, rate: int = 16000) -> bytes:
    """Gera PCM 16-bit mono de silêncio."""
    n = int(rate * duration_sec)
    return struct.pack("<" + "h" * n, *([0] * n))


def make_beep_pcm(freq: float = 880, duration_sec: float = 0.5, rate: int = 16000, amp: int = 20000) -> bytes:
    """Gera um beep PCM 16-bit mono (simula 'alguém falando')."""
    n = int(rate * duration_sec)
    samples = [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)]
    return struct.pack("<" + "h" * n, *samples)


async def try_connect(model: str) -> bool:
    """Tenta abrir uma sessão live com o modelo dado. Retorna True se funcionou."""
    print(f"\n[TENTATIVA] modelo = {model}")
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_INSTRUCTION,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(
            language_code="pt-BR",
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            ),
        ),
    )
    client = genai.Client(api_key=API_KEY)
    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            print(f"  [OK] Sessão aberta com sucesso!")
            # Estratégia: manda um turno de TEXTO explícito forçando a IA a responder.
            # Isso valida o caminho de RECEBER áudio da IA (que é o que importa pro interfone).
            # (O caminho de ENTRADA de áudio do visitante só faz sentido com fala real.)
            await session.send_realtime_input(text="Ola, boa tarde. Quero falar com o Mauricio, por favor.")
            print(f"  [OK] Turno de texto enviado. Aguardando resposta falada da IA (até 12s)...")

            # Recebe mensagens por até 8s
            received_audio = 0
            received_text = ""
            try:
                async def recv_loop():
                    nonlocal received_audio, received_text
                    async for message in session.receive():
                        # Debug: mostra a estrutura de cada mensagem
                        print(f"  [MSG] type={type(message).__name__} attrs={[a for a in dir(message) if not a.startswith('_') and getattr(message,a,None) is not None]}")
                        sc = getattr(message, "server_content", None)
                        if sc:
                            # Áudio
                            parts = getattr(sc, "model_turn", None)
                            if parts and getattr(parts, "parts", None):
                                for part in parts.parts:
                                    blob = getattr(part, "inline", None) or getattr(part, "inline_data", None)
                                    if blob and getattr(blob, "data", None):
                                        received_audio += len(blob.data)
                                        print(f"  [AUDIO] chunk de {len(blob.data)} bytes (mime={getattr(blob,'mime_type',None)})")
                            # Transcrição de saída
                            ot = getattr(sc, "output_transcription", None)
                            if ot and getattr(ot, "text", None):
                                received_text += ot.text
                            # Transcrição de entrada
                            it = getattr(sc, "input_transcription", None)
                            if it and getattr(it, "text", None):
                                print(f"  [INPUT] visitante disse: {it.text!r}")
                            # Turno completo
                            if getattr(sc, "turn_complete", False):
                                print("  [TURN] turno completo.")
                                break
                        # Tool call etc (ignoramos aqui)

                await asyncio.wait_for(recv_loop(), timeout=12.0)
            except asyncio.TimeoutError:
                print("  [INFO] Timeout de 12s esperando resposta.")

            print(f"  [RESULT] Áudio recebido: {received_audio} bytes")
            print(f"  [RESULT] Texto transcrito da IA: {received_text!r}")
            if received_audio > 0:
                print(f"  [✓] MODELO {model} FUNCIONA — áudio recebido!")
                return True
            return False
    except Exception as e:
        msg = str(e)[:200]
        print(f"  [FALHA] {type(e).__name__}: {msg}")
        return False


async def main():
    print("=" * 60)
    print("  SMOKE TEST - Gemini Live API")
    print("=" * 60)
    if not API_KEY:
        print("GEMINI_API_KEY nao configurada. Defina a variavel de ambiente antes de testar.")
        return
    print("API key: configurada")
    print(f"Modelos candidatos: {MODEL_CANDIDATES}")

    for model in MODEL_CANDIDATES:
        ok = await try_connect(model)
        if ok:
            print(f"\n{'=' * 60}")
            print(f"  ✓ SUCESSO! Use este modelo: {model}")
            print(f"{'=' * 60}")
            return
        # pequena pausa entre tentativas
        await asyncio.sleep(1.0)

    print(f"\n{'=' * 60}")
    print("  ✗ NENHUM modelo funcionou. Verifique a API key e os nomes de modelo.")
    print("{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
