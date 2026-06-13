# BMO: Autism Companion Robot

BMO is an AI-powered social companion robot designed for autistic children. It runs entirely on a Raspberry Pi 5 (or Windows PC for development), featuring a local real-time voice pipeline, automated therapy engine, multi-modal emotion detection, and a parent dashboard.

## 🛠️ Prerequisites

If you are running this on your PC to test, you will need:
- Python 3.11 or higher
- A working microphone and speakers
- A webcam (for engagement and emotion tracking)

## 🚀 Setup Instructions

### 1. Install Dependencies
Open your terminal/command prompt in this folder and run:
```bash
pip install -r requirements.txt
```

### 2. Configure API Keys
Copy the `.env.example` file and rename it to `.env`:
```bash
# On Windows PowerShell
Copy-Item .env.example .env

# On Linux/Mac
cp .env.example .env
```
Open the `.env` file in a text editor and add your **Groq API Key** (and optionally Gemini). You can get a free Groq key at [console.groq.com](https://console.groq.com).

### 3. Setup Models
The TTS system requires ONNX voice models. Create a `models/` directory in the root of the project:
```bash
mkdir models
```
You need to place the following files inside the `models/` folder:
- `bmo.onnx` (Your custom voice model, if you have it)
- `voices.bin` (Kokoro voice profiles)
- *(If you don't have a custom voice, BMO will try to fallback to `kokoro-v1.0.onnx`, so download it from the Kokoro HuggingFace repo).*

### 4. Run the Application
Start the robot by running the main entry point:
```bash
python main.py
```

## 🎮 What Happens When It Runs?
1. **Database Setup**: The SQLite database (`data/bmo.db`) will be automatically created and migrated.
2. **Dashboard**: A Flask web server will start in the background. You can open your browser and navigate to `http://localhost:5000` to view the Parent Dashboard.
3. **Robot Face**: A Pygame window will open displaying BMO's face.
4. **Voice Pipeline**: BMO will start listening. Try speaking to it to start a session!

## 🧪 Testing
If you want to run the test suite to ensure the subsystems are working before starting the main loop, run:
```bash
pytest robot/tests/
```
