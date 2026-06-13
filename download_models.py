import urllib.request
import os
from pathlib import Path

def download_file(url, destination):
    print(f"Downloading {url} to {destination}...")
    try:
        urllib.request.urlretrieve(url, destination)
        print("Download complete!")
    except Exception as e:
        print(f"Failed to download: {e}")

if __name__ == "__main__":
    models_dir = Path(__file__).parent / "models"
    models_dir.mkdir(exist_ok=True)
    
    voices_url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.json"
    voices_dest = models_dir / "voices.json"
    
    if not voices_dest.exists():
        download_file(voices_url, voices_dest)
    else:
        print("voices.json already exists.")
