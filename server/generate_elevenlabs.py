import os
import requests
import subprocess
import tempfile

API_KEY = "sk_0e3aca15ce43ba5da40ade91c4cc594783d3726e160cdb5b"

QUICK_RESPONSES = {
    "resp_descendo.raw": "O morador já está descendo, aguarde um momento.",
    "resp_aguarde.raw": "Aguarde dois minutos, o morador já está a caminho.",
    "resp_ausente.raw": "O morador não se encontra no momento. Se possível, volte mais tarde.",
    "resp_abrir.raw": "Pode entrar, vou liberar a porta."
}

def get_voice_id(target_name="Ana"):
    # Como a chave não tem permissão de leitura, precisamos que o usuário coloque o ID real da voz.
    # Por padrão, vamos usar a 'Alice' gringa, mas ele precisa substituir.
    return "ORgG8rwdAiMYRug8RJwR"

def generate_and_convert(text, output_raw_path, voice_id):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    headers = {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json"
    }
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
            "style": 0.0,
            "use_speaker_boost": True
        }
    }
    
    print(f"Gerando MP3 para: '{text}'...")
    response = requests.post(url, json=data, headers=headers)
    
    if response.status_code != 200:
        print(f"Erro na geração ({response.status_code}): {response.text}")
        return
        
    mp3_data = response.content
    
    # Salvar mp3 num temp file
    temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    temp_mp3.write(mp3_data)
    temp_mp3.close()
    
    print(f"MP3 gerado. Extraindo e convertendo para PCM 8000Hz via FFMPEG...")
    # Executar ffmpeg para exportar para PCM s16le, 8000Hz monocanal
    cmd = [
        "ffmpeg", "-y", "-i", temp_mp3.name,
        "-f", "s16le", # PCM 16-bit
        "-ac", "1",    # Mono
        "-ar", "8000", # 8000 Hz
        "-af", "volume=2.0", # Da um ganhozinho para ficar bom no interfone
        output_raw_path
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"[OK] Salvo em {output_raw_path}")
    except Exception as e:
        print("Erro ao executar ffmpeg:", e)
    finally:
        os.remove(temp_mp3.name)

def main():
    voice_id = get_voice_id("Ana Alice")
    print(f"Voice ID selecionado: {voice_id}")
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    for filename, text in QUICK_RESPONSES.items():
        out_path = os.path.join(current_dir, filename)
        generate_and_convert(text, out_path, voice_id)

if __name__ == "__main__":
    main()
