# Hosting the Platform on a Mac (Ubuntu Virtual Machine)

How to run the platform on an Intel Mac that keeps macOS, inside an Ubuntu Server virtual machine. The deployment itself is unchanged: [`deploy-baremetal.sh`](deploy-baremetal.sh) runs inside the VM.

> This procedure has not been executed end to end yet. Record what differs on your machine in the feedback log.

## Design

```
 ESP32 node ──Wi-Fi──► Mac (macOS)                       Ubuntu VM
 browser    ──LAN───►  <mac-ip>:1883  ──forwarded──►  :1883  Mosquitto
                       <mac-ip>:8080  ──forwarded──►  :80    Caddy -> dashboard
                       127.0.0.1:2222 ──forwarded──►  :22    SSH (from the Mac only)
```

- **Tool: VirtualBox** (free, open source). The Extension Pack is not needed.
- **Network: NAT with port forwarding**, not a bridge. Sensor nodes and browsers talk to the Mac's own address, so it works the same on Wi-Fi and Ethernet, and the VM has no address of its own to manage.
- The dashboard is on port **8080** of the Mac: forwarding to port 80 would require running VirtualBox as root.

## 1. Prepare the Mac

1. **Fixed address.** In your router, reserve the Mac's IP address (DHCP reservation). The sensor firmware is compiled with this address. Note it as `<mac-ip>` (System Settings > Network).
2. **Never sleep.** A sleeping Mac stops the VM.

   ```bash
   sudo pmset -a sleep 0 disksleep 0 displaysleep 10
   sudo pmset -a disablesleep 1          # stay awake with the lid closed
   ```

   Keep the Mac on its charger. Prefer leaving the lid open: it cools better.
3. **No surprise restart.** System Settings > General > Software Update > Automatic Updates: turn off "Install macOS updates".
4. **Firewall.** If the macOS firewall is on (System Settings > Network > Firewall), allow incoming connections for VirtualBox when macOS asks at the first start of the VM.
5. **Battery.** A laptop left on its charger for weeks can develop a swollen battery. Put it on a hard, clear surface and look at it during the weekly check.

## 2. Install VirtualBox and Create the VM

1. Download VirtualBox for "macOS / Intel hosts" from <https://www.virtualbox.org/wiki/Downloads> and install it. macOS asks to allow the system extension (System Settings > Privacy & Security), then to restart.
2. Download the **Ubuntu Server 24.04 LTS** ISO (amd64) from <https://ubuntu.com/download/server>.
3. In VirtualBox, **New**:

   | Setting | Value |
   | :--- | :--- |
   | Name | `iot-airquality` |
   | ISO image | the Ubuntu Server ISO; tick "Skip Unattended Installation" |
   | Memory | 2048 MB |
   | Processors | 2 |
   | Disk | 20 GB |

4. Start the VM and follow the Ubuntu installer with the defaults. Choose a user name and password, and tick **Install OpenSSH server**. Do not select any extra package.
5. When the installer asks to reboot, let it restart, log in once in the VM window, then shut the VM down:

   ```bash
   sudo timedatectl set-timezone Europe/Paris     # the daily report runs at 07:00 local time
   sudo poweroff
   ```

## 3. Forward the Ports and Start Headless

In Terminal on the Mac, with the VM powered off:

```bash
VBoxManage modifyvm iot-airquality --natpf1 "ssh,tcp,127.0.0.1,2222,,22"
VBoxManage modifyvm iot-airquality --natpf1 "mqtt,tcp,,1883,,1883"
VBoxManage modifyvm iot-airquality --natpf1 "dashboard,tcp,,8080,,80"
VBoxManage startvm iot-airquality --type headless
ssh -p 2222 <user>@127.0.0.1                     # you are now inside the VM
```

## 4. Install the Platform in the VM

Inside the VM (through the SSH session above):

```bash
sudo apt-get update && sudo apt-get install -y git build-essential
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"

git clone https://github.com/fl00/iot-airquality-platform.git
cd iot-airquality-platform
make build
sudo ./deploy/deploy-baremetal.sh
```

If the repository cannot be cloned from GitHub (not pushed yet, or private), carry it as a single file instead:

```bash
# 1. On the development PC, in the repository
git bundle create iot-airquality.bundle main

# 2. Move iot-airquality.bundle to the Mac (USB key, AirDrop, shared folder), then on the Mac
scp -P 2222 iot-airquality.bundle <user>@127.0.0.1:~/

# 3. In the VM, instead of the `git clone https://...` line above
git clone ~/iot-airquality.bundle iot-airquality-platform
```

The script ends with the state of the five services: all must be `active`.

## 5. Check from Outside the VM

| Check | Command or action | Expected |
| :--- | :--- | :--- |
| Dashboard from the Mac | open `http://127.0.0.1:8080/` | "En attente de mesures" page |
| Dashboard from another device on the LAN | open `http://<mac-ip>:8080/` | Same page |
| Broker reachable from the LAN | from another computer: `nc -vz <mac-ip> 1883` | Connection succeeded |

Then configure the sensor node (`firmware/include/secrets.h`):

```c
#define MQTT_BROKER_HOST   "<mac-ip>"
#define MQTT_BROKER_PORT   1883
#define MQTT_USER          "sensor"
#define MQTT_PASSWORD      "<from the VM: sudo cat /etc/iot-airquality/sensor-credentials>"
```

Flash it; its card appears on the dashboard within a few seconds.

Set up alerts as described in [`README.md`](README.md#alerts) (`ALERT_URL` in `/etc/iot-airquality/env`, inside the VM).

## 6. Start the VM Automatically

So that the platform comes back after a restart of the Mac:

1. System Settings > Users & Groups: enable **automatic login** for your user. (Not available while FileVault is on; without it, log in once after each restart.)
2. Create `~/Library/LaunchAgents/local.iot-airquality-vm.plist`:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
     <key>Label</key><string>local.iot-airquality-vm</string>
     <key>ProgramArguments</key>
     <array>
       <string>/usr/local/bin/VBoxManage</string>
       <string>startvm</string>
       <string>iot-airquality</string>
       <string>--type</string>
       <string>headless</string>
     </array>
     <key>RunAtLoad</key><true/>
   </dict>
   </plist>
   ```

3. Load it, then verify by restarting the Mac once:

   ```bash
   launchctl load ~/Library/LaunchAgents/local.iot-airquality-vm.plist
   ```

Inside the VM, every service starts by itself at boot.

## Daily Use

| Action | Command (on the Mac) |
| :--- | :--- |
| Open a shell in the VM | `ssh -p 2222 <user>@127.0.0.1` |
| State of the VM | `VBoxManage list runningvms` |
| Stop the VM cleanly | `VBoxManage controlvm iot-airquality acpipowerbutton` |
| Start it | `VBoxManage startvm iot-airquality --type headless` |
| Update the platform | in the VM: `git pull && make build && sudo ./deploy/deploy-baremetal.sh` (with a bundle: copy the new file, then `git pull ~/iot-airquality.bundle main`) |

Always stop the VM cleanly before shutting the Mac down: a hard stop loses the samples the ingestor holds in memory.

## What Differs from a Dedicated Linux Host

- The dashboard address is `http://<mac-ip>:8080/`, not port 80.
- The weekly checks of [`docs/V1_VALIDATION.md`](../docs/V1_VALIDATION.md) are run inside the VM, through SSH.
- A sleeping, restarted or logged-out Mac stops data collection. The daily health report runs inside the VM, so it cannot report that the Mac itself is down: the sign is a missing line in `health.csv`, and, with the dashboard open, every sensor shown "Hors ligne".
