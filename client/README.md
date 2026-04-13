# Temperature Reporter — ESP32 Firmware

Reads temperatures from DS18B20 probes every second and POSTs each reading
as JSON to the Flask monitoring server.

---

## Project layout

```
temp_reporter/
├── CMakeLists.txt
├── README.md
├── components/            ← external libraries cloned here
│   ├── esp32-owb/
│   └── esp32-ds18b20/
└── main/
    ├── CMakeLists.txt
    ├── Kconfig.projbuild
    ├── app_main.c
    ├── wifi.c
    └── wifi.h
```

---

## Prerequisites

### 1 — ESP-IDF v5.0

Install ESP-IDF v5.0 by following the official guide:
https://docs.espressif.com/projects/esp-idf/en/v5.0/esp32/get-started/

Activate the environment:

```bash
. $HOME/esp/esp-idf/export.sh   # Linux/macOS
# or
%IDF_PATH%\export.bat            # Windows CMD
```

### 2 — Clone the 1-Wire and DS18B20 component libraries

From inside the project root:

```bash
mkdir -p components
git clone https://github.com/DavidAntliff/esp32-owb    components/esp32-owb
git clone https://github.com/DavidAntliff/esp32-ds18b20 components/esp32-ds18b20
```

> **ESP-IDF v5 compatibility note:**  
> The `owb_rmt` driver uses the RMT peripheral. If the cloned versions do not
> build cleanly against ESP-IDF v5.0, check each repo's branches/issues for a
> v5-compatible branch, or use the `idf-component-manager` versions if
> available. The rest of the application code is v5-compatible.

### 3 — Hardware wiring

| Signal | DS18B20 pin | ESP32 GPIO |
|--------|-------------|------------|
| GND    | 1 (GND)     | GND        |
| Data   | 2 (DQ)      | GPIO 4 *(configurable)* |
| VDD    | 3 (VDD)     | 3.3 V      |

Add a 4.7 kΩ pull-up resistor between the data line and VDD.

---

## Configuration (`menuconfig`)

```bash
idf.py menuconfig
```

Navigate to **Temperature Reporter Configuration** and set:

| Field | Description |
|-------|-------------|
| **WiFi SSID** | Your WPA2-Enterprise network name |
| **EAP anonymous identity** | Outer identity (see WPA2-Enterprise notes below) |
| **EAP username** | Your inner/real username |
| **EAP password** | Your password |
| **Flask server base URL** | e.g. `http://192.168.1.42:5000` |
| **1-Wire GPIO pin** | GPIO number connected to DS18B20 data line (default: 4) |

---

## Connecting to a WPA2-Enterprise network

WPA2-Enterprise (IEEE 802.1X) is the authentication standard used by most
corporate and university WiFi networks. Unlike WPA2-Personal (which uses a
single shared passphrase), it authenticates individual users with a username
and password via an EAP protocol.

This firmware uses **EAP-PEAP with MSCHAPv2** as the inner method, which is
the most common configuration (eduroam, most corporate networks).

### What to enter in menuconfig

**SSID**  
The network name exactly as it appears in your WiFi scan (case-sensitive).

**EAP anonymous identity** (`CONFIG_EAP_IDENTITY`)  
The *outer* identity. This is sent in plain text before the encrypted tunnel
is established, so network operators often allow it to be anonymous. Common
values:
- Leave blank `""` — works on most networks
- `"anonymous"` — generic fallback
- `"anonymous@yourdomain.com"` — required by some RADIUS configurations
  (replace `yourdomain.com` with your institution's domain)

Ask your network administrator if unsure.

**EAP username** (`CONFIG_EAP_USERNAME`)  
Your actual username, sent *inside* the encrypted EAP tunnel. This is the
same credential you use to log in to your institution's systems:
- University networks: your student/staff ID (e.g. `jsmith`)
- Corporate networks: your domain username (e.g. `CORP\jsmith` or `jsmith`)

**EAP password** (`CONFIG_EAP_PASSWORD`)  
Your password, also sent inside the EAP tunnel.

### eduroam

eduroam follows the same EAP-PEAP/MSCHAPv2 scheme:

| Field | Value |
|-------|-------|
| SSID | `eduroam` |
| Anonymous identity | `anonymous@youruniversity.edu` |
| Username | `yourlogin@youruniversity.edu` (include the domain) |
| Password | Your university password |

### CA certificate validation

This demo skips CA certificate validation for simplicity. This means the
device will connect to any RADIUS server claiming to be your network. For a
production deployment, supply your RADIUS server's CA certificate via
`esp_eap_client_set_ca_cert()` in `wifi.c` to prevent man-in-the-middle
attacks.

---

## Build and flash

```bash
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor   # adjust port as needed
```

On Windows the port is typically `COM3`, `COM4`, etc.

Press `Ctrl+]` to exit the serial monitor.

---

## Verifying operation

On startup the serial monitor will show:

```
I (1234) wifi: Connecting to "MyNetwork" (WPA2-Enterprise / EAP-PEAP)...
I (2345) wifi: Got IP address: 192.168.1.77
I (2346) wifi: WiFi connected successfully
I (2800) temp_reporter: Time synchronised
I (4900) temp_reporter: Scanning 1-Wire bus for DS18B20 devices...
I (5100) temp_reporter:   [0] 28EEB2A52C160215
I (5101) temp_reporter: Found 1 device
I (5900) temp_reporter: Sample 1:
I (5901) temp_reporter:   [0] 28EEB2A52C160215: 23.1 °C
```

You can also verify delivery directly:

```bash
curl http://<server-ip>:5000/api/data
```

---

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `Disconnected — retrying...` loop | Wrong SSID, username, or password |
| Connects but no IP | DHCP issue; check network config |
| `No DS18B20 devices found` | Wiring fault or missing pull-up resistor |
| `POST failed: ESP_ERR_HTTP_CONNECT` | Wrong server URL or server not running |
| Temperature reads as `85.0 °C` | Parasitic power issue; check VDD wiring |
