import asyncio
import os
import math
import struct
from openai import AsyncOpenAI

os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")

client = AsyncOpenAI()

def increase_volume(pcm_bytes, factor):
    out = bytearray()
    for i in range(0, len(pcm_bytes)-1, 2):
        sample = struct.unpack('<h', pcm_bytes[i:i+2])[0]
        sample = int(sample * factor)
        if sample > 32767: sample = 32767
        elif sample < -32768: sample = -32768
        out.extend(struct.pack('<h', sample))
    return bytes(out)

async def generate_tts_pcm(text, output_path):
    print(f"Gerando voz IA (PCM bruto): {text} -> {output_path}")
    response = await client.audio.speech.create(
        model="tts-1",
        voice="onyx",
        input=text,
        response_format="pcm",
        speed=0.85 # Deixa a voz artificial mais pausada e lenta
    )
    # response.read() fetches binary directly in the stable client
    pcm_bytes = response.read() 
    
    # Aumenta o volume físico em 2.2x (220%)
    louder_pcm = increase_volume(pcm_bytes, 2.2)
    
    with open(output_path, "wb") as f:
        f.write(louder_pcm)

def generate_ding_dong():
    sample_rate = 24000
    amp = 28000.0 # Aumentando muito o volume do Ding Dong
    pcm = bytearray()
    
    pcm.extend(bytes(8000))
    for i in range(int(sample_rate * 0.4)):
        pcm.extend(struct.pack('<h', int(amp * math.sin(2 * math.pi * 750.0 * i / sample_rate))))
    for i in range(int(sample_rate * 0.6)):
        pcm.extend(struct.pack('<h', int(amp * math.sin(2 * math.pi * 600.0 * i / sample_rate))))
    pcm.extend(bytes(8000))
    
    return bytes(pcm)

async def main():
    target_dir = os.path.join(os.path.dirname(__file__), "..", "main")
    ola_path = os.path.join(target_dir, "ola_esp32.raw")
    minuto_path = os.path.join(target_dir, "minuto_esp32.raw")
    
    await generate_tts_pcm("Olá, com quem você gostaria de falar?", ola_path)
    
    with open(ola_path, "rb") as f:
        voz_bytes = f.read()
    with open(ola_path, "wb") as f:
        f.write(generate_ding_dong() + voz_bytes)
        
    await generate_tts_pcm("Só um minuto, vou chamar.", minuto_path)
    
    with open(minuto_path, "rb") as f:
        minuto_bytes = f.read()
    with open(minuto_path, "wb") as f:
        f.write(bytes(24000) + minuto_bytes)
        
    print("\n[+] Arquivos de áudio gerados mais altos (220%) e mais devagar com sucesso!")

if __name__ == "__main__":
    asyncio.run(main())
