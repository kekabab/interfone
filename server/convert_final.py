import os
import glob
import math
import struct
import subprocess
import tempfile

DOWNLOAD_DIR = r"C:\Users\kekab\Downloads"
SERVER_DIR = r"C:\Users\kekab\.gemini\antigravity\scratch\esp32-ai-intercom\server"
MAIN_DIR = r"C:\Users\kekab\.gemini\antigravity\scratch\esp32-ai-intercom\main"

def generate_ding_dong():
    sample_rate = 24000
    amp = 28000.0
    pcm = bytearray()
    # Ding (750 Hz) for 0.4s
    for i in range(int(sample_rate * 0.4)):
        pcm.extend(struct.pack('<h', int(amp * math.sin(2 * math.pi * 750.0 * i / sample_rate))))
    # Dong (600 Hz) for 0.6s
    for i in range(int(sample_rate * 0.6)):
        pcm.extend(struct.pack('<h', int(amp * math.sin(2 * math.pi * 600.0 * i / sample_rate))))
    pcm.extend(bytes(8000)) # silence padding
    return bytes(pcm)

def generate_beep():
    sample_rate = 24000
    amp = 20000.0
    pcm = bytearray()
    pcm.extend(bytes(8000))
    # Beep (1000 Hz) for 0.3s
    for i in range(int(sample_rate * 0.3)):
        pcm.extend(struct.pack('<h', int(amp * math.sin(2 * math.pi * 1000.0 * i / sample_rate))))
    return bytes(pcm)

def convert_to_pcm(input_path, sample_rate, volume=2.2):
    temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".raw")
    temp_wav.close()
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", input_path,
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate),
        "-af", f"volume={volume}",
        temp_wav.name
    ]
    subprocess.run(cmd, check=True)
    with open(temp_wav.name, "rb") as f:
        data = f.read()
    os.remove(temp_wav.name)
    return data

def main():
    search_pattern = os.path.join(DOWNLOAD_DIR, "ElevenLabs*.mp3")
    files = glob.glob(search_pattern)
    files.sort(key=os.path.getmtime)
    
    if len(files) < 4:
        print("Faltam arquivos.")
        return
        
    target_files = files[-4:]
    
    # Audio 1: Olá + Ding Dong
    pcm1 = convert_to_pcm(target_files[0], 24000)
    with open(os.path.join(MAIN_DIR, "ola_esp32.raw"), "wb") as f:
        f.write(generate_ding_dong() + pcm1)
        
    # Audio 2: Minuto + Beep
    pcm2 = convert_to_pcm(target_files[1], 24000)
    with open(os.path.join(MAIN_DIR, "minuto_esp32.raw"), "wb") as f:
        f.write(pcm2 + generate_beep())
        
    # Audio 3: Descendo
    pcm3 = convert_to_pcm(target_files[2], 8000)
    with open(os.path.join(SERVER_DIR, "resp_descendo.raw"), "wb") as f:
        f.write(pcm3)
        
    # Audio 4: Ausente
    pcm4 = convert_to_pcm(target_files[3], 8000)
    with open(os.path.join(SERVER_DIR, "resp_ausente.raw"), "wb") as f:
        f.write(pcm4)
        
    print("Sucesso!")

if __name__ == "__main__":
    main()
