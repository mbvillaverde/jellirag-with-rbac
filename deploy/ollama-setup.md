# Ollama Setup for JellieRAG

This guide covers setting up Ollama on macOS for use with JellieRAG over Tailscale.

## Installation

### 1. Install Ollama

Download and install Ollama from: https://ollama.com/download

```bash
# Verify installation
ollama --version
```

### 2. Pull Required Models

JellieRAG requires both an LLM and an embeddings model:

```bash
# LLM for chat (7B parameter model)
ollama pull qwen2.5:7b

# Embeddings model (768-dimensional)
ollama pull nomic-embed-text

# Verify models are available
ollama list
```

**Model Selection:**
- **qwen2.5:7b**: Good balance of speed and quality (~30–50 tok/s on M4 Pro Metal)
- **nomic-embed-text**: 768-dim embeddings, high quality for semantic search
- **Alternative**: qwen2.5:14b for higher quality (fits in 24GB memory, ~15–25 tok/s)

## Network Configuration

### 3. Bind Ollama to All Interfaces

By default, Ollama binds to loopback only. We need it accessible over Tailscale:

```bash
# Set environment variable for Ollama to listen on all interfaces
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"

# Alternative: Bind specifically to Tailscale interface
# First find your Tailscale IP:
tailscale ip -4
# Then use:
# launchctl setenv OLLAMA_HOST "<tailscale-ip>:11434"
```

### 4. Keep Models Loaded Longer

Reduce cold-start penalties by keeping models in memory:

```bash
# Keep models loaded for 30 minutes instead of default 5 minutes
launchctl setenv OLLAMA_KEEP_ALIVE "30m"
```

### 5. Restart Ollama

```bash
# Restart Ollama to apply environment variable changes
# You can do this from the Ollama menu bar icon
# Or use:
killall ollama
open -a Ollama
```

### 6. Verify Binding

```bash
# Check Ollama is listening
lsof -i :11434

# Should show Ollama listening on 0.0.0.0:11434 (not just 127.0.0.1)

# Test access from localhost
curl http://localhost:11434/api/tags
```

## Power Management

### 7. Prevent Sleep on AC Power

Keep Ollama available by preventing system sleep when on AC:

```bash
# Start caffeinate (prevents sleep while running)
caffeinate -is &

# Add to login items for persistence:
# System Preferences → Users & Groups → Login Items → Add → Caffeinate
```

### Alternative: Energy Saver Settings

```bash
# Disable "Power Nap" if enabled
# Set "Prevent computer from sleeping automatically when the display is off"
```

## Testing

### 8. Test Tailscale Connectivity

```bash
# From the MacBook, find your Tailscale IP
tailscale ip -4

# Test connectivity to Ollama via Tailscale IP
curl http://<tailscale-ip>:11434/api/tags

# Should return JSON list of available models
```

### 9. Test from JellieRAG LXC

```bash
# In the JellieRAG LXC, test connectivity to Ollama
curl http://<macbook-tailscale-ip>:11434/api/tags

# Test embeddings
curl -X POST http://<macbook-tailscale-ip>:11434/api/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"nomic-embed-text","input":"test text"}'

# Test chat (with streaming)
curl -X POST http://<macbook-tailscale-ip>:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen2.5:7b","messages":[{"role":"user","content":"hello"}],"stream":true}'
```

## Performance Tuning

### GPU Acceleration

Ollama automatically uses GPU acceleration on supported Macs:

```bash
# Check Metal support
ollama run qwen2.5:7b "test"

# Monitor GPU usage during inference
# Activity Monitor → GPU History
```

### Memory Management

```bash
# Check model memory usage
ollama ps

# Expected memory for qwen2.5:7b: ~4-6GB RAM
# Expected memory for nomic-embed-text: ~300MB RAM
```

### Model Loading

```bash
# Warm up models to avoid cold-start on first user request
ollama run qwen2.5:7b "hi" --keepalive 30m
ollama run nomic-embed-text "test" --keepalive 30m
```

## Troubleshooting

### Ollama Not Binding to Tailscale

```bash
# Check if OLLAMA_HOST is set
launchctl getenv OLLAMA_HOST

# If not set, re-run the setenv command and restart Ollama
```

### Models Not Loading

```bash
# Check available models
ollama list

# Re-pull if missing
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### Connection Refused

```bash
# Verify Ollama is running
ps aux | grep ollama

# Check if port 11434 is listening
lsof -i :11434

# Test from another machine on tailnet
curl http://<macbook-tailscale-ip>:11434/api/tags
```

### Slow First Response

```bash
# Model cold-start adds ~5-15 seconds
# Warm up models before use:
ollama run qwen2.5:7b "hello"

# Increase OLLAMA_KEEP_ALIVE if experiencing frequent cold starts
launchctl setenv OLLAMA_KEEP_ALIVE "60m"
```

## Alternative Configurations

### Use Different Models

```bash
# Higher quality but slower
ollama pull qwen2.5:14b

# Update JellieRAG .env:
# LLM_MODEL=qwen2.5:14b
```

### Use Hosted Provider Fallback

When MacBook is offline, swap to hosted provider:

```bash
# In JellieRAG .env file:
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<your-groq-api-key>
LLM_MODEL=llama-3.1-8b-instant

# Keep embeddings local (if preferred)
EMBED_BASE_URL=http://<macbook-tailscale-ip>:11434/v1
EMBED_API_KEY=ollama
EMBED_MODEL=nomic-embed-text
```

## Monitoring

### Check Model Status

```bash
# See loaded models
ollama ps

# See model details
ollama show qwen2.5:7b
ollama show nomic-embed-text
```

### Monitor Inference

```bash
# Run with verbose output for debugging
ollama run qwen2.5:7b "test message" --verbose

# Check system resources during inference
# Activity Monitor → CPU / GPU / Memory
```

## Advanced: Model Quantization

For memory-constrained systems:

```bash
# Pull quantized models (smaller, faster, slightly lower quality)
# qwen2.5:7b already has good quantization support
# For custom models, use:
ollama create my-model -f Modelfile
```

## Cleanup

```bash
# Remove unused models
ollama rm <model-name>

# Clear cache
rm -rf ~/.ollama/cache

# Reinstall Ollama
rm -rf /Applications/Ollama.app
rm -rf ~/.ollama
# Re-download from ollama.com
```