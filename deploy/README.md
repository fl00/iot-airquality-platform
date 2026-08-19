# Bare-Metal Operations & Cloud Infrastructure (`deploy/`)

## 1. System Architecture & Port Mapping

```
  Internet (Port 80/443)
           │
           ▼
    ┌──────────────┐
    │ Caddy Proxy  │  (Port 80 / 443 -> 8000, SSE unbuffered)
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │ FastHTML App │◄──────── Direct IPC Stream ────────┐
    │ (Port 8000)  │        (:9100/stream SSE 16B)      │
    └──────┬───────┘                                    │
           │                                            │
           │ Direct SQL                                 │
           ▼                                            │
    ┌──────────────┐                             ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │  InfluxDB    │◄──── Micro-Batch Writes ────┤ Rust Ingest  │◄────┤  Mosquitto   │◄────┤ ESP32 Nodes  │
    │ (Port 8086)  │       (Line Protocol)       │ (Port 9100)  │      │ (Port 1883)  │      │ (Protobuf)   │
    └──────────────┘                             └──────┬───────┘      └──────────────┘      └──────────────┘
                                                        │
                                                        ▼
                                                Prometheus / Metrics
                                              (Port 9100, Standard 0.0.4)
```

---

## 2. Infrastructure as Code (Azure Bicep)

Deploys an ultra-lean `Standard_B1s_v2` VM (1 vCPU, 1 GiB RAM, ~$3.80/month):

```bash
az deployment group create \
  --resource-group rg-iot-airquality \
  --template-file iac/main.bicep \
  --parameters adminPublicKey="$(cat ~/.ssh/id_rsa.pub)"
```

---

## 3. Automated Bare-Metal Provisioning

```bash
# Run bare-metal provisioning on target Linux node
sudo ./deploy-baremetal.sh
```

### Tuning & Sandboxing Included
- **InfluxDB Memory Ceiling:** Hardcapped at 32MB storage cache.
- **Kernel Tuning (`sysctl`):** TCP Fast Open, minimal TCP send buffer latency (`tcp_notsent_lowat`), connection recycling (`tcp_tw_reuse`).
- **Hardened Systemd Services:** `MemoryMax=40M`, `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `NoNewPrivileges=true`.
