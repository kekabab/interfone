import os, glob
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

DOWNLOAD_DIR = r"C:\Users\kekab\Downloads"

def main():
    search_pattern = os.path.join(DOWNLOAD_DIR, "ElevenLabs*.mp3")
    files = glob.glob(search_pattern)
    files.sort(key=os.path.getmtime)
    
    print(f"Encontrados {len(files)} audios.")
    # Ignore the first one as user requested (it's 22 min older)
    # the user said: "o primeiro q faz 22 min nao conta... so os demais os ultimos 5"
    if len(files) > 5:
        target_files = files[-5:]
    else:
        target_files = files
        
    for i, path in enumerate(target_files):
        try:
            with open(path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                  model="whisper-1", 
                  file=f
                )
            print(f"Áudio {i+1}: {transcription.text}")
        except Exception as e:
            print(f"Erro no Áudio {i+1}: {e}")

if __name__ == "__main__":
    main()
