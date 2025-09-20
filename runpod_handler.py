import runpod
from api.src.pipelines.kokoro_v1 import KokoroV1Pipeline # Adjust import path if needed
import base64
import io

# Load your model ONCE when the container starts
# This object will be reused for all subsequent requests
pipeline = KokoroV1Pipeline(use_gpu=True)
print("TTS Pipeline Initialized.")

def handler(job):
    """
    The handler function that RunPod will call for each job.
    """
    job_input = job['input']
    
    text = job_input.get('text', 'Hello, this is a test.')
    speaker_wav_base64 = job_input.get('speaker_wav_base64') # Expecting a base64 encoded wav
    
    if not speaker_wav_base64:
        return {"error": "speaker_wav_base64 is a required field."}
        
    # Decode the speaker wav from base64
    speaker_wav_bytes = base64.b64decode(speaker_wav_base64)
    
    # Generate the audio
    # The pipeline.tts() method likely returns raw audio bytes
    wav_bytes = pipeline.tts(text=text, speaker_wav=speaker_wav_bytes)

    # Convert the output audio bytes to a base64 string for JSON response
    wav_base64 = base64.b64encode(wav_bytes).decode('utf-8')

    return {
        "audio_base64": wav_base64
    }

# Start the RunPod serverless worker
runpod.serverless.start({"handler": handler})