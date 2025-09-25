# Realtime TTS Streaming Guide


📜 Introduction
This guide provides a complete, step-by-step plan for building and deploying a high-performance, real-time Text-to-Speech (TTS) system. The core of this system is the Kokoro TTS model, served via a concurrent FastAPI application and consumed by a Django web frontend.
The primary goal is to achieve a seamless, low-latency user experience where audio begins playing almost instantly and is visualized in real-time, eliminating the long waits associated with traditional TTS generation.
Core Concepts:
 * Blocking TTS: The slow method. The server generates the entire audio file, then sends it. The user waits for the whole process.
 * Streaming TTS: The fast, real-time method. The server sends audio data in small chunks as it's being generated. The frontend plays these chunks immediately, creating the perception of an instant response.
🏛️ System Architecture
We will build a decoupled, scalable system with three main components running in Docker containers.
 * Django Web App: Your main application. Its primary role is to handle business logic and serve the HTML/JavaScript frontend to the user.
 * Kokoro TTS Service: A dedicated, standalone FastAPI application. It exposes an API endpoint that takes text and returns a stream of raw PCM audio data. This service will be optimized to handle multiple concurrent requests.
 * Reverse Proxy (Handled by Dokploy): An intermediary that routes incoming traffic. Requests for your main site (/) go to the Django app, while requests for TTS (/api/tts/) go to the Kokoro service.
This decoupled architecture is crucial for scalability and modularity.
🛠️ Part 1: The TTS Backend (Kokoro-FastAPI)
Here, we will configure the Kokoro-FastAPI application to handle multiple simultaneous users efficiently on a CPU server.
Step 1: The Dockerfile
We will modify the original Dockerfile to include Gunicorn, a production-grade process manager that will run multiple instances of our TTS application (workers).
Dockerfile
FROM python:3.10-slim 

# Install dependencies and check espeak location
# Rust is required to build sudachipy and pyopenjtalk-plus
RUN apt-get update -y &&  \
    apt-get install -y espeak-ng espeak-ng-data git libsndfile1 curl ffmpeg g++ && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    mkdir -p /usr/share/espeak-ng-data && \
    ln -s /usr/lib/*/espeak-ng-data/* /usr/share/espeak-ng-data/ && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/ && \
    mv /root/.local/bin/uvx /usr/local/bin/ && \
    curl https://sh.rustup.rs -sSf | sh -s -- -y && \
    useradd -m -u 1000 appuser && \
    mkdir -p /app/api/src/models/v1_0 && \
    chown -R appuser:appuser /app

USER appuser
WORKDIR /app

# Copy dependency files
COPY --chown=appuser:appuser pyproject.toml ./pyproject.toml

# Install dependencies with CPU extras AND GUNICORN
RUN . /home/appuser/.cargo/env && \
    uv venv --python 3.10 && \
    uv sync --extra cpu --no-cache && \
    uv pip install gunicorn

# Copy project files including models
COPY --chown=appuser:appuser api ./api
COPY --chown=appuser:appuser web ./web
COPY --chown=appuser:appuser docker/scripts/ ./
RUN chmod +x ./entrypoint.sh

# Set environment variables
ENV PATH="/home/appuser/.cargo/bin:/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/api \
    UV_LINK_MODE=copy \
    USE_GPU=false \
    PHONEMIZER_ESPEAK_PATH=/usr/bin \
    PHONEMIZER_ESPEAK_DATA=/usr/share/espeak-ng-data \
    ESPEAK_DATA_PATH=/usr/share/espeak-ng-data \
    DEVICE="cpu" \
    DOWNLOAD_MODEL=true

# Download model if enabled
RUN if [ "$DOWNLOAD_MODEL" = "true" ]; then \
    . .venv/bin/activate && \
    python download_model.py --output api/src/models/v1_0; \
    fi

# Run FastAPI server through entrypoint.sh
CMD ["./entrypoint.sh"]

Step 2: The Entrypoint Script
This script now launches Gunicorn, which in turn manages multiple Uvicorn workers. This is the key to handling concurrent users.
entrypoint.sh
#!/bin/bash
set -e

# This part remains the same
if [ "$DOWNLOAD_MODEL" = "true" ]; then
    . .venv/bin/activate
    python download_model.py --output api/src/models/v1_0
fi

# THE FIX: Use Gunicorn to manage multiple Uvicorn workers.
# For a 4-core CPU server, use (2 * 4) + 1 = 9 workers. Adjust if you have a 2-core CPU (use 5).
echo "Starting Gunicorn with 9 Uvicorn workers..."
exec gunicorn -w 9 -k uvicorn.workers.UvicornWorker api.src.main:app --bind 0.0.0.0:8880 --log-level info --timeout 120

Step 3: Docker Compose for Deployment
This docker-compose.yml file defines both your Django application and the Kokoro TTS service. Dokploy can use this file to manage your deployment.
docker-compose.yml
version: '3.8'

services:
  django_app:
    build:
      context: . # Assuming your Django Dockerfile is in the root directory
      dockerfile: Dockerfile.django
    restart: always
    ports:
      - "8000:8000"
    command: gunicorn myproject.wsgi:application --bind 0.0.0.0:8000 # Example command
    depends_on:
      - kokoro_tts

  kokoro_tts:
    build:
      context: ./kokoro-fastapi # Assumes the kokoro-fastapi project is in a subfolder
      dockerfile: docker/cpu/Dockerfile # Path to the Kokoro Dockerfile from the previous step
    restart: always
    ports:
      - "8880:8880"
    environment:
      # ONNX Optimization Settings for vectorized operations
      - ONNX_NUM_THREADS=8
      - ONNX_INTER_OP_THREADS=4
      - ONNX_EXECUTION_MODE=parallel
      - ONNX_OPTIMIZATION_LEVEL=all
      - ONNX_MEMORY_PATTERN=true
      - ONNX_ARENA_EXTEND_STRATEGY=kNextPowerOfTwo

🎨 Part 2: The Frontend (Django & JavaScript)
Now we'll set up the client-side to call our new TTS service, play the audio stream, and visualize it.
Step 1: Django Setup (View and URL)
Django's role is simple: serve the main page.
yourapp/views.py
from django.views.generic import TemplateView

class TTSFrontendView(TemplateView):
    template_name = "yourapp/index.html"

yourproject/urls.py
from django.urls import path
from yourapp.views import TTSFrontendView

urlpatterns = [
    path('', TTSFrontendView.as_view(), name='home'),
]

Step 2: The HTML Template
This file contains the user interface: a text area, a button, and a canvas for the visualization.
yourapp/templates/yourapp/index.html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Real-Time TTS</title>
    <style>
        body { font-family: sans-serif; background: #282c34; color: white; display: flex; flex-direction: column; align-items: center; padding-top: 50px; }
        textarea { width: 500px; height: 100px; margin-bottom: 20px; background: #3c4049; color: white; border: 1px solid #555; border-radius: 5px; padding: 10px; }
        button { padding: 10px 20px; font-size: 16px; cursor: pointer; border: none; border-radius: 5px; background: #61afef; color: white; }
        #visualizer { width: 500px; height: 150px; background: #21252b; margin-top: 20px; border-radius: 5px; }
        #status { margin-top: 15px; font-style: italic; color: #999; }
    </style>
</head>
<body>
    <h1>Real-Time TTS Streaming</h1>
    <textarea id="tts-input">Hello world. This is a real-time text to speech demonstration with a sound visualizer.</textarea>
    <button id="speak-button">Speak</button>
    <canvas id="visualizer"></canvas>
    <p id="status">Ready.</p>

    <script src="{% static 'js/main.js' %}"></script>
</body>
</html>

Step 3: The Magic (JavaScript) ✨
This script handles fetching the stream, playing the audio through the Web Audio API, and powering the visualizer.
static/js/main.js
document.addEventListener('DOMContentLoaded', () => {
    const speakButton = document.getElementById('speak-button');
    const ttsInput = document.getElementById('tts-input');
    const statusEl = document.getElementById('status');
    const canvas = document.getElementById('visualizer');
    const canvasCtx = canvas.getContext('2d');

    // --- Configuration ---
    const KOKORO_API_ENDPOINT = 'http://localhost:8880/v1/audio/speech'; // Use your actual IP/domain in production
    const KOKORO_SAMPLE_RATE = 24000;

    let audioContext;
    let analyser;
    let animationFrameId;

    speakButton.addEventListener('click', () => {
        const textToSpeak = ttsInput.value;
        if (!textToSpeak) return;

        // Reset state and start streaming
        if (audioContext) {
            audioContext.close();
        }
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }
        clearCanvas();
        streamAndPlayAudio(textToSpeak);
    });

    function setupAudioApi() {
        audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: KOKORO_SAMPLE_RATE });
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 2048; // Standard FFT size
        analyser.connect(audioContext.destination); // Connect analyser to output
    }
    
    async function streamAndPlayAudio(text) {
        setupAudioApi();
        speakButton.disabled = true;
        statusEl.textContent = 'Connecting to TTS service...';

        let nextPlayTime = 0;
        
        try {
            const response = await fetch(KOKORO_API_ENDPOINT, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model: "kokoro",
                    input: text,
                    voice: "af_sky",
                    response_format: "pcm",
                    stream: true
                })
            });

            if (!response.ok) throw new Error(`API Error: ${response.statusText}`);
            if (!response.body) throw new Error("ReadableStream not supported.");

            const reader = response.body.getReader();
            statusEl.textContent = 'Streaming audio...';
            startVisualization(); // Start drawing the visualizer

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    statusEl.textContent = 'Stream finished. Ready.';
                    speakButton.disabled = false;
                    setTimeout(() => cancelAnimationFrame(animationFrameId), 1000); // Stop viz a second after audio ends
                    break;
                }
                
                // Convert the incoming Uint8Array (16-bit PCM) to a Float32Array
                const pcmData = new Int16Array(value.buffer);
                const float32Data = new Float32Array(pcmData.length);
                for (let i = 0; i < pcmData.length; i++) {
                    float32Data[i] = pcmData[i] / 32768.0; // Normalize to [-1.0, 1.0]
                }

                const audioBuffer = audioContext.createBuffer(1, float32Data.length, audioContext.sampleRate);
                audioBuffer.copyToChannel(float32Data, 0);

                const source = audioContext.createBufferSource();
                source.buffer = audioBuffer;
                source.connect(analyser); // Connect audio source to the analyser

                const currentTime = audioContext.currentTime;
                const scheduleTime = nextPlayTime < currentTime ? currentTime : nextPlayTime;
                
                source.start(scheduleTime);
                nextPlayTime = scheduleTime + audioBuffer.duration;
            }

        } catch (error) {
            console.error("TTS Stream Error:", error);
            statusEl.textContent = `Error: ${error.message}`;
            speakButton.disabled = false;
        }
    }

    function startVisualization() {
        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        function draw() {
            animationFrameId = requestAnimationFrame(draw);
            analyser.getByteTimeDomainData(dataArray); // Get waveform data

            canvasCtx.fillStyle = '#21252b';
            canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
            canvasCtx.lineWidth = 2;
            canvasCtx.strokeStyle = '#61afef';
            canvasCtx.beginPath();

            const sliceWidth = canvas.width * 1.0 / bufferLength;
            let x = 0;

            for (let i = 0; i < bufferLength; i++) {
                const v = dataArray[i] / 128.0; // Normalize
                const y = v * canvas.height / 2;

                if (i === 0) {
                    canvasCtx.moveTo(x, y);
                } else {
                    canvasCtx.lineTo(x, y);
                }
                x += sliceWidth;
            }

            canvasCtx.lineTo(canvas.width, canvas.height / 2);
            canvasCtx.stroke();
        }
        draw();
    }
    
    function clearCanvas() {
        canvasCtx.fillStyle = '#21252b';
        canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
    }
});

🔄 Part 3: Modular Architecture for Easy Swapping
Your system is already well-designed for swapping models. Because the JavaScript frontend calls the TTS service directly, all you need to do is deploy a different TTS model in a Docker container and update the frontend configuration.
The key is to ensure the new model's API adheres to the same contract: it must accept a POST request and return a streaming PCM audio response.
Swapping to Piper TTS (CPU)
 * Deploy Piper: Create a new Dockerfile for a Piper TTS FastAPI server. Ensure it has a streaming endpoint (e.g., /api/tts/piper-stream) that returns audio/pcm.
 * Update JavaScript: Change the configuration in main.js.
   // const KOKORO_API_ENDPOINT = '...';
const PIPER_API_ENDPOINT = 'http://localhost:8881/api/tts/piper-stream';

// In the fetch call:
const response = await fetch(PIPER_API_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        text: text, // Piper might use different parameter names
        voice: "en_US-ljspeech-medium"
    })
});

Swapping to VoxCPM (GPU)
 * Deploy VoxCPM: Deploy the VoxCPM Docker container on a GPU-enabled server (like a Hetzner GPU instance). Expose its streaming endpoint.
 * Update JavaScript:
   const VOXCPM_API_ENDPOINT = 'http://your-gpu-server-ip:port/stream';

// In the fetch call:
const response = await fetch(VOXCPM_API_ENDPOINT, {
    method: 'POST',
    // ... update headers and body for VoxCPM's API spec
});

By abstracting the TTS service into its own container and having a single point of configuration in the JS, you can easily experiment with and switch models without touching your core Django application.
🔍 Part 4: Troubleshooting Guide
| Problem | Symptom | Solution(s) |
|---|---|---|
| CORS Error | Browser console shows "Cross-Origin Request Blocked..." | Your Django domain is trying to call your TTS domain. You must enable CORS in the Kokoro-FastAPI main.py file to allow requests from your Django app's domain. See the previous response for the FastAPI CORS middleware code. |
| No Sound | The stream appears to start and finish with no errors, but no audio plays. | 1. Check your speakers. 2. Verify the sampleRate in the JavaScript AudioContext matches the model's output (24000 for Kokoro). 3. Open browser dev tools and inspect the network response to ensure audio data is actually being received. |
| Choppy/Glitched Audio | The audio stutters, clicks, or sounds robotic. | 1. Server Overload: Your CPU is not keeping up. Check htop on the server. You may need to reduce the number of Gunicorn workers or upgrade your server. 2. Network Latency: A poor connection between the user and the server can cause buffer underruns. This is harder to solve but indicates the need for a CDN or servers closer to your users. |
| Container Fails to Start | The kokoro_tts container crashes or is in a restart loop in Dokploy. | Use docker logs kokoro_tts (or check logs in Dokploy's UI). Common errors include Python dependency conflicts, issues downloading the model, or incorrect paths in the entrypoint.sh script. |
| 502 Bad Gateway | Your reverse proxy (Dokploy) can't reach the TTS service. | This almost always means the kokoro_tts container has crashed. Check its logs to find the root cause. |
✅ Conclusion
By following this guide, you will have a robust, scalable, and extremely fast real-time TTS system. The architecture is modular, allowing for future upgrades, and the user experience will be state-of-the-art.
⚙️ Part 5: Edge Cases & Advanced Considerations
While the primary setup works for most scenarios, real-world applications often present unique challenges. Here are some edge cases to consider:
 * Extremely Long Text Inputs: A user pasting a whole chapter of a book could tie up a worker for minutes. This can lead to a Denial-of-Service (DoS) scenario, even with multiple workers.
   * Solution: Implement input length validation. In your Django view or JavaScript, enforce a character limit (e.g., 1000 characters) before sending the request to the TTS API. For longer texts, implement a chunking mechanism where the text is split into sentences and sent in sequential requests.
 * Non-Standard Characters & Emojis: TTS models can behave unpredictably with characters they weren't trained on, such as emojis (😊) or complex Unicode symbols (✅). This can cause the model to fail or produce garbage audio.
   * Solution: Sanitize your input. Before sending the text to the API, use a Python or JavaScript function to strip or replace unsupported characters. A simple regex to remove emojis is a good starting point.
 * Rapid, Repetitive API Calls: A user repeatedly clicking the "Speak" button can trigger multiple simultaneous streams, consuming server resources and potentially creating overlapping audio on the client side.
   * Solution: Implement client-side debouncing and state management. The "Speak" button should be disabled (speakButton.disabled = true;) as soon as a request is sent and only re-enabled after the stream finishes or fails. You should also explicitly cancel any existing audio context or stream before starting a new one.
 * Browser/Network Incompatibility: Older browsers may not support the Web Audio API or ReadableStream. Users on slow or unstable mobile networks might experience significant audio buffering or dropouts.
   * Solution: Feature detection and graceful degradation. Check for the existence of window.AudioContext and response.body in your JavaScript. If they are not available, you can fall back to the non-streaming (blocking) endpoint, which is slower but more compatible, and display a message to the user like, "Your browser is in compatibility mode; audio may take longer to start."
🩺 Part 6: In-Depth Troubleshooting
This section expands on the basic troubleshooting guide with more detailed steps.
| Symptom | Potential Cause | Detailed Diagnostic Steps & Solutions |
|---|---|---|
| Very High "Time to First Byte" (TTFB) | 1. Model is slow to initialize. <br> 2. CPU is underpowered. <br> 3. Network latency between services. | 1. Check Kokoro Logs: Look for the model loading time when the worker starts. If it's slow on every request, it means the model isn't being cached in memory properly. <br> 2. Monitor Server CPU: Use the htop command on your Hetzner server while sending a request. If all CPU cores immediately spike to 100% and stay there, your server might be too small for the number of workers you've configured. Try reducing the worker count in your entrypoint.sh. <br> 3. Internal Docker Networking: Ensure your Django and Kokoro containers are on the same Docker network for low-latency communication. docker-compose handles this automatically. |
| Audio Cuts Off Mid-Stream | 1. Gunicorn worker timed out. <br> 2. Client-side script error. <br> 3. Reverse proxy (Dokploy/Nginx) timeout. | 1. Increase Gunicorn Timeout: The default timeout might be too short for long text. In your entrypoint.sh, add the --timeout 120 flag to the gunicorn command to increase it to 120 seconds. <br> 2. Check Browser Console: Look for any JavaScript errors that might have interrupted the while loop that reads the stream. Wrap the stream-reading logic in a try...catch...finally block to ensure resources are cleaned up even if an error occurs. <br> 3. Check Proxy Config: If you're using Dokploy or another reverse proxy, check its settings for stream or request timeouts and increase them if necessary. |
| Garbled or "Alien" Sound | 1. Incorrect sampleRate. <br> 2. Incorrect PCM data interpretation. | 1. Verify Sample Rate: Double-check that the sampleRate in your new AudioContext({ sampleRate: 24000 }) exactly matches what the Kokoro model outputs. A mismatch is the most common cause of pitch-shifted or garbled audio. <br> 2. Inspect Data Conversion: Re-verify the JavaScript logic that converts the Int16Array to a Float32Array. Ensure the division is correct (pcmData[i] / 32768.0). An off-by-one error or incorrect data type can corrupt the audio signal. |
| Memory Leak in Container | A worker process consumes more and more RAM over time, eventually crashing. | This is often caused by the TTS model not properly releasing GPU or CPU memory after inference. <br> Solution: Configure Gunicorn to gracefully restart workers after a certain number of requests. Add the --max-requests 1000 and --max-requests-jitter 50 flags to your gunicorn command. This will automatically restart each worker after it has handled around 1000 requests, effectively clearing its memory. |
✨ Part 7: Best Practices
Following these best practices will lead to a more robust, secure, and maintainable system.
 * Use Environment Variables for Configuration: Never hardcode values like API endpoints, ports, or secret keys. Use environment variables in your docker-compose.yml and your code (os.getenv() in Python, process.env in JS frameworks). This makes it easy to switch between development, staging, and production environments.
 * Implement Health Check Endpoints: Add a simple /health endpoint to your Kokoro-FastAPI application that returns a 200 OK response. Dokploy or other orchestrators can periodically ping this endpoint to ensure the service is alive and automatically restart it if it fails.
 * Secure Your API: Your TTS API is open to the public. To prevent abuse, consider implementing API key authentication. Your Django backend can generate a temporary token that the JavaScript frontend uses to authenticate with the TTS service for each session.
 * Optimize Your Docker Image: For production, use multi-stage builds in your Dockerfile. The first stage builds the application and installs dependencies, and the final, smaller stage copies only the necessary runtime files. This reduces the image size, leading to faster deployments and a smaller attack surface.
 * Asynchronous Django (Advanced): While the current architecture correctly offloads the streaming work to the TTS service, for very high-traffic sites, you might consider making your Django views asynchronous (async def my_view(...)) and using an ASGI server like Uvicorn for Django as well. This can improve the efficiency of the web server that serves your frontend.
📚 Part 8: Resources & Further Reading
To deepen your understanding, explore these resources:
 * Web Audio API:
   * MDN Documentation: The definitive guide for all Web Audio API nodes and concepts. MDN Web Audio API
   * "A Tale of Two Clocks": An excellent article explaining the timing model (AudioContext.currentTime) for perfect, gapless audio scheduling. Link to article
 * Docker & Gunicorn:
   * Awesome Docker: A curated list of Docker resources and tools. GitHub - Awesome Docker
   * Gunicorn Design: The official documentation explaining Gunicorn's process model and worker types. Gunicorn Design
 * TTS Models & AI:
   * Hugging Face Spaces: A great place to discover and test new TTS models. Many have public demos running in Docker containers. Hugging Face Spaces
   * The Illustrated Transformer: A foundational article for understanding the architecture behind many modern AI models. Jay Alammar's Blog
🚀 Part 9: Pre-flight Checklist & Deployment Workflow
This section provides a concrete, step-by-step workflow for the AI agent to follow, ensuring a smooth deployment from local development to production on Hetzner with Dokploy.
Objective: To provide a repeatable, testable deployment process.
 * Environment Setup (Local):
   * Ensure Docker and Docker Compose are installed.
   * Clone the main Django application repository and the kokoro-fastapi repository into their respective folders as outlined in the docker-compose.yml file.
   * Create a .env file in the project root to manage environment variables (e.g., DJANGO_SECRET_KEY, TTS_API_URL).
 * Local Verification (docker-compose up):
   * Build the images: Run docker-compose build to build both the Django and Kokoro TTS images.
   * Launch the services: Run docker-compose up.
   * Test endpoints:
     * Navigate to http://localhost:8000 to ensure the Django app loads.
     * Use a tool like curl or Postman to hit the Kokoro health check endpoint (http://localhost:8880/health) and the streaming endpoint to verify they work.
     * Perform a full end-to-end test using the web interface.
 * Dokploy Deployment on Hetzner:
   * Repository Setup: Ensure your project (with the docker-compose.yml and all necessary Dockerfiles) is pushed to a Git repository (like GitHub or GitLab).
   * Connect Dokploy: Connect Dokploy to your Git repository.
   * Configure Application:
     * Point Dokploy to the docker-compose.yml file in your repository.
     * Set Environment Variables: In the Dokploy UI, add all necessary environment variables from your .env file. Crucially, ensure the JavaScript fetch URL is updated to use the public domain of your TTS service, not localhost.
     * Configure Persistent Storage (Optional but Recommended): If you allow users to upload speaker .wav files, configure a persistent volume in Dokploy for the directory where these files are stored. This ensures they aren't lost if the container restarts.
     * Set up Reverse Proxy & Domains: Use Dokploy's interface to assign a public domain/subdomain to both the django_app service and the kokoro_tts service. Configure the reverse proxy to route traffic accordingly.
 * Post-Deployment Verification:
   * Check the deployment logs in Dokploy for any errors.
   * Access the public URL of your Django application.
   * Perform a live end-to-end test to ensure the frontend can successfully call the TTS service and stream audio.
🩺 Part 10: Monitoring, Logging, and Maintenance
A deployed application needs to be monitored to ensure it's healthy and performing well.
 * Centralized Logging:
   * Gunicorn Access Logs: The current gunicorn command sends logs to standard output. Dokploy automatically captures these container logs, which is great for debugging. For more advanced logging, you can configure Gunicorn to output structured logs (e.g., JSON) that can be sent to a logging service like Grafana Loki or Datadog.
   * FastAPI Logging: Within your FastAPI app (api/src/main.py), you can configure Python's logging module to add more detailed application-level logs, such as timing how long each TTS inference takes or logging errors with more context.
 * Performance Monitoring:
   * Hetzner Cloud Console: Use the built-in graphs in your Hetzner Cloud console to monitor the server's overall CPU, memory, and network usage. If you see sustained high CPU usage, it may be time to upgrade your server or optimize your worker count.
   * API Metrics with Prometheus: For a more advanced setup, you can add a library like starlette-prometheus to your FastAPI app. This exposes a /metrics endpoint with detailed performance data (e.g., request latency, error rates) that a Prometheus server can scrape. Dokploy often has easy integrations for Prometheus and Grafana for creating dashboards.
 * Maintenance Plan:
   * Dependency Updates: Regularly (e.g., quarterly), check for updates to your key dependencies (kokoro-tts model, fastapi, uvicorn, gunicorn, and the base Docker image). Use a tool like pip-audit or GitHub's Dependabot to automate security vulnerability scanning.
   * Model Updates: Keep an eye on the Kokoro-FastAPI GitHub repository for new model versions, which may offer better performance or quality.
   * Regular Backups: If you store any user data (like custom voice .wav files), ensure you have a regular backup strategy for your persistent volumes, which can often be configured in Hetzner's control panel.
🛡️ Part 11: Security Hardening
Security is not an afterthought. Here are critical steps to secure your application.
 * Use a Reverse Proxy with Rate Limiting: This is your first line of defense against abuse. Configure Dokploy's underlying reverse proxy (e.g., Traefik, Nginx) to rate-limit requests to your /v1/audio/speech endpoint. For example, you could limit each IP address to 10 requests per minute. This single step can prevent a malicious actor from overwhelming your service.
 * Run Containers as a Non-Root User: The provided Dockerfile already does this (useradd appuser, USER appuser), which is excellent. This is a critical security best practice that limits the potential damage if an attacker gains control of the container.
 * Manage Secrets Securely: Never commit secrets (like API keys, database passwords, or Django's SECRET_KEY) to your Git repository. Always inject them as environment variables through the Dokploy UI.
 * Limit Container Privileges: When deploying, ensure the container is run with the minimum required privileges. Dokploy handles this well by default. Avoid running containers in --privileged mode unless absolutely necessary.
 * Input Validation and Sanitization: As mentioned in the edge cases, rigorously validate and sanitize all user input before it reaches the model. This prevents potential injection attacks or crashes from unexpected data formats.
🔄 Part 12: Keeping Up-to-Date (As of Late 2025)
The AI landscape moves incredibly fast. The information in this guide is current, but here’s how to ensure the agent uses the most up-to-date tools and techniques.
 * Python Version: Python 3.10 is a solid choice. However, by the time of implementation, Python 3.12 or 3.13 will be stable. It's recommended to use the latest stable version of Python for performance and security benefits. The Dockerfile should be updated accordingly (e.g., FROM python:3.12-slim).
 * Package Management: The use of uv is very forward-looking and an excellent choice, as it's significantly faster than pip. This is a modern best practice.
 * Model Repositories: Before final implementation, the agent should check the original GitHub repositories for Kokoro-FastAPI, Piper, and VoxCPM. Look at the recent commit history and releases to see if there have been significant updates to the model, API structure, or deployment recommendations.
 * FastAPI & Uvicorn: Check the official FastAPI and Uvicorn documentation. They are mature projects, but new features or performance improvements are released regularly. The Gunicorn worker class (uvicorn.workers.UvicornWorker) is the established standard and is unlikely to change.
