# JellieRAG Deployment Guide

## Architecture Overview

JellieRAG runs as a fully local homelab deployment with no Cloudflare dependencies:

- **Frontend**: Astro SSR (:3000) + Vue 3 islands
- **Backend**: FastAPI (:8000) with local SQLite + sqlite-vec
- **AI Providers**: OpenAI-compatible HTTP clients (default: Ollama on MacBook over Tailscale)
- **Networking**: Tailscale-only with auto Let's Encrypt certs via `tailscale serve`

## Deployment Topology

```
┌──────────────────────────────────────────────────────────────────┐
│  Homelab hypervisor (Proxmox or similar, 4 vCPU / 16GB)          │
│                                                                  │
│  ┌──────────────────┐   ┌────────────────────────────┐          │
│  │ LXC: jellyfin    │   │ LXC: jellirag              │          │
│  │  Tailscale dev   │   │  Tailscale device          │          │
│  │  jellyfin.<tn>   │   │  jellirag.<tn>             │          │
│  │  .ts.net:8096    │   │  .ts.net → :3000           │          │
│  └──────────────────┘   │  (tailscale serve --bg)    │          │
│                         │                            │          │
│                         │  docker compose:           │          │
│                         │   - frontend (astro :3000) │          │
│                         │   - backend  (fastapi:8000)│          │
│                         │   - volume: /var/jellirag/ │          │
│                         │       jellyrag.db          │          │
│                         └────────────────────────────┘          │
└──────────────────────────────────────────────────────────────────┘
               ▲                                ▲
               │ tailnet                        │ tailnet (LLM_BASE_URL,
               │                                │  EMBED_BASE_URL)
               │                                │
        ┌──────┴───────┐                ┌───────┴────────────┐
        │ family       │                │ MacBook M4 Pro     │
        │ devices      │                │  Ollama :11434     │
        │ (chat users) │                │  bound to tailscale│
        └──────────────┘                │  qwen2.5:7b        │
                                        │  nomic-embed-text  │
                                        └────────────────────┘
```

## Prerequisites

- Proxmox hypervisor (4 vCPU / 16GB recommended)
- Tailscale account with ACLs configured
- Operator's MacBook M4 Pro (or similar with GPU acceleration)
- Ollama installed on the MacBook

## Step 1: Create LXCs

### Jellyfin LXC
```bash
# Create LXC (adjust parameters as needed)
pct create 100 local:vztmpl/debian-12-template_*.tar.zst \
  --hostname jellyfin \
  --cores 2 \
  --memory 4096 \
  --storage local-lvm:100 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp

# Start and configure
pct start 100
pct enter 100
```

### JellieRAG LXC
```bash
# Create LXC
pct create 200 local:vztmpl/debian-12-template_*.tar.zst \
  --hostname jellirag \
  --cores 2 \
  --memory 2048 \
  --storage local-lvm:50 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp

# Start and configure
pct start 200
pct enter 200
```

## Step 2: Install Tailscale in Each LXC

**Important:** Proxmox writes a default `resolv.conf` inside LXCs that assumes Tailscale is installed *in the LXC* for MagicDNS resolution to function.

### In both LXCs (Jellyfin and JellieRAG):
```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Start and authenticate
tailscale up

# Verify MagicDNS works (should resolve your tailnet names)
nslookup jellyfin.<tailnet>.ts.net
```

## Step 3: Setup Ollama on MacBook

### Install Ollama
```bash
# Install Ollama from https://ollama.com/download

# Pull required models
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

### Configure Ollama for Tailnet Access
```bash
# Bind Ollama to all interfaces (including Tailscale)
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"

# Keep models loaded longer to avoid cold-start penalties
launchctl setenv OLLAMA_KEEP_ALIVE "30m"

# Prevent sleep killing Ollama when on AC (optional)
caffeinate -is &
```

### Find MacBook's Tailnet IP
```bash
# From the MacBook
tailscale ip -4
```

## Step 4: Deploy JellieRAG

### In the JellieRAG LXC:
```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Clone repository (or copy files)
cd /opt
git clone <your-repo-url> jellirag
cd jellirag

# Create volume directory
mkdir -p /var/jellirag /var/backups/jellirag
chown -R 1000:1000 /var/jellirag /var/backups/jellirag
```

### Configure Environment Variables
```bash
# Create .env file
cat > /opt/jellirag/.env << 'EOF'
# AI providers (OpenAI-compatible)
LLM_BASE_URL=http://<macbook-tailnet-ip>:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5:7b
EMBED_BASE_URL=http://<macbook-tailnet-ip>:11434/v1
EMBED_API_KEY=ollama
EMBED_MODEL=nomic-embed-text
EMBED_DIM=768
LLM_TIMEOUT_SECONDS=5

# Database
SQLITE_PATH=/var/jellirag/jellyrag.db

# Sync service
SYNC_EMBED_CONCURRENCY=4

# Homelab overlay (Tailscale-only)
JELLYFIN_TAILSCALE_URL=http://jellyfin.<tailnet>.ts.net:8096
JELLYFIN_API_KEY=<your-jellyfin-api-key>
JELLYFIN_DEEPLINK_BASE=http://jellyfin.<tailnet>.ts.net:8096

# Conversation lifecycle
SESSION_TTL_DAYS=30

# Auth perimeter
JWT_SECRET=<generate-long-random-string>
JWT_TTL_DAYS=7
BOOTSTRAP_ADMIN_EMAIL=<your-email>
BOOTSTRAP_ADMIN_PASSWORD=<your-password>

# CORS (should match tailscale serve URL)
FRONTEND_ORIGIN=https://jellirag.<tailnet>.ts.net

# Sync schedule (cron)
SYNC_CRON=0 3 * * *
EOF
```

### Start Services
```bash
cd /opt/jellirag/deploy
docker-compose up -d
```

## Step 5: Configure tailscale serve

### In the JellieRAG LXC:
```bash
# Publish frontend with auto Let's Encrypt cert
tailscale serve --bg --https 443 http://localhost:3000

# Verify it's running
tailscale serve status
```

### Validate Access
```bash
# From a family device on the tailnet
curl https://jellirag.<tailnet>.ts.net

# Should return the frontend
```

## Step 6: Setup Backup Cron

### In the JellieRAG LXC:
```bash
# Add nightly backup cron (runs at 3 AM)
cat > /etc/cron.d/jellirag-backup << 'EOF'
0 3 * * * root sqlite3 /var/jellirag/jellyrag.db ".backup '/var/backups/jellirag/jellyrag-$(date +\%F).db'"
EOF

# Create logrotate for backups
cat > /etc/logrotate.d/jellirag-backups << 'EOF'
/var/backups/jellirag/jellyrag-*.db {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 644 root root
}
EOF
```

## Step 7: Initial Sync and Testing

### Trigger Initial Library Sync
```bash
# Login as admin (use bootstrap credentials)
curl -X POST https://jellirag.<tailnet>.ts.net/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"<your-email>","password":"<your-password>"}'

# Save the JWT token and trigger sync
curl -X POST https://jellirag.<tailnet>.ts.net/api/sync \
  -H "Authorization: Bearer <jwt-token>"
```

### Test Chat Interface
```bash
# Open in browser
https://jellirag.<tailnet>.ts.net

# Test a query about your media library
```

## Fallback Procedure

When the MacBook is offline, you can swap to a hosted provider:

### Use Groq (Free Alternative)
```bash
# Edit .env file in JellieRAG LXC
vim /opt/jellirag/.env

# Change:
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_API_KEY=<your-groq-api-key>
LLM_MODEL=llama-3.1-8b-instant

# Restart backend
cd /opt/jellirag/deploy
docker-compose restart backend
```

### Restore from Backup
```bash
# Stop services
docker-compose down

# Copy backup
cp /var/backups/jellirag/jellyrag-2026-06-22.db /var/jellirag/jellyrag.db

# Restart services
docker-compose up -d
```

## Scaling Considerations

### sqlite-vec Performance
- **≤10k chunks**: Single-digit-ms query latency (typical family library)
- **50–100k+ chunks**: May require migration to dedicated vector database (Qdrant)

### Migration Path to Qdrant
1. Export chunks from SQLite
2. Set up Qdrant instance
3. Create collection with appropriate dimensions
4. Import chunks with embeddings
5. Update `db.py` to use Qdrant client instead of sqlite-vec

## Troubleshooting

### Ollama Unreachable
```bash
# Test connectivity from JellieRAG LXC
ping <macbook-tailnet-ip>
telnet <macbook-tailnet-ip> 11434

# Check Ollama is running and accessible
curl http://<macbook-tailnet-ip>:11434/api/tags
```

### Tailnet Resolution Issues
```bash
# Verify Tailscale is running in LXC
tailscale status

# Check MagicDNS resolution
nslookup jellyfin.<tailnet>.ts.net
nslookup <macbook-tailnet-hostname>
```

### Database Lock Issues
```bash
# Check SQLite locks
sqlite3 /var/jellirag/jellyrag.db ".timeout 5000" "SELECT * FROM chunks LIMIT 1"
```

## Security Notes

- **Tailnet-only access**: No public ingress, all traffic stays inside WireGuard mesh
- **JWT + RBAC**: Per-user data scoping maintained even with tailnet auth
- **Ollama no auth**: Protected by tailnet ACLs, accessible only to authorized devices
- **Deep links fail closed**: Only resolve when client is on the tailnet

## Maintenance

### Updates
```bash
cd /opt/jellirag
git pull
cd deploy
docker-compose up -d --build
```

### Monitor Logs
```bash
# Backend logs
docker logs -f jellirag-backend-1

# Frontend logs
docker logs -f jellirag-frontend-1
```

### Check Disk Usage
```bash
# Database size
ls -lh /var/jellirag/jellyrag.db

# Backup retention
ls -lh /var/backups/jellirag/
```