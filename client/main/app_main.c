/*
 * Temperature Reporter — ESP-IDF v5.0
 *
 * Reads temperatures from DS18B20 probes every second and POSTs each reading
 * as JSON to the Flask monitoring server.  Also polls the server for the
 * current fan duty cycle and applies it via PWM.
 *
 * POST /data/batch  [ { "probe_id": "<ROM>", "temperature": <float>,
 *                       "timestamp": "<ISO-8601>" }, ... ]
 * GET  /fan/duty    → { "duty": <0–100> }
 */

#include <string.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_sntp.h"
#include "esp_system.h"
#include "nvs_flash.h"

#include "owb.h"
#include "owb_rmt.h"
#include "ds18b20.h"

#include "wifi.h"
#include "fan.h"

/* ── configuration ──────────────────────────────────────────────────────── */

#define CONFIG_SERVER_URL "http://" CONFIG_SERVER_ADDRESS ":5000"

#define GPIO_DS18B20        (CONFIG_ONE_WIRE_GPIO)
#define MAX_DEVICES         (8)
#define DS18B20_RESOLUTION  (DS18B20_RESOLUTION_12_BIT)
#define SAMPLE_PERIOD_MS    (1000)

/* Buffer large enough for one JSON object per probe plus array brackets.
 * Each object is at most ~90 bytes; 8 probes → ~720 bytes + overhead. */
#define BATCH_PAYLOAD_MAX   (1024)

/* Receive buffer for the GET /fan/duty response body. */
#define HTTP_RX_BUF_SIZE    (64)

static const char *TAG = "temp_reporter";

/* ── SNTP helpers ───────────────────────────────────────────────────────── */

static void sntp_sync(void)
{
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, "pool.ntp.org");
    esp_sntp_init();

    ESP_LOGI(TAG, "Waiting for SNTP time sync...");
    struct tm timeinfo = {0};
    int retries = 0;
    while (timeinfo.tm_year < (2020 - 1900) && retries++ < 20) {
        vTaskDelay(pdMS_TO_TICKS(500));
        time_t now;
        time(&now);
        gmtime_r(&now, &timeinfo);
    }
    if (timeinfo.tm_year >= (2020 - 1900)) {
        ESP_LOGI(TAG, "Time synchronised");
    } else {
        ESP_LOGW(TAG, "SNTP sync timed out — server will timestamp readings");
    }
}

static bool get_timestamp(char *buf, size_t len)
{
    time_t now;
    struct tm timeinfo;
    time(&now);
    gmtime_r(&now, &timeinfo);
    if (timeinfo.tm_year < (2020 - 1900)) {
        return false;
    }
    strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    return true;
}

/* ── HTTP: post temperatures ─────────────────────────────────────────────── */

static void post_temperatures(int count,
                               const char probe_ids[][OWB_ROM_CODE_STRING_LENGTH],
                               float *temperatures)
{
    char timestamp[32];
    char payload[BATCH_PAYLOAD_MAX];
    int  idx = 0;
    bool have_ts = get_timestamp(timestamp, sizeof(timestamp));

    payload[idx++] = '[';

    for (int i = 0; i < count; ++i) {
        int remaining = (int)sizeof(payload) - idx;
        if (remaining <= 2) {
            ESP_LOGE(TAG, "Payload buffer too small — sending first %d readings", i);
            break;
        }
        int written;
        if (have_ts) {
            written = snprintf(payload + idx, remaining,
                               "{\"probe_id\":\"%s\",\"temperature\":%.2f"
                               ",\"timestamp\":\"%s\"}",
                               probe_ids[i], temperatures[i], timestamp);
        } else {
            written = snprintf(payload + idx, remaining,
                               "{\"probe_id\":\"%s\",\"temperature\":%.2f}",
                               probe_ids[i], temperatures[i]);
        }
        if (written < 0 || written >= remaining) {
            ESP_LOGE(TAG, "snprintf error or truncation at probe %d", i);
            break;
        }
        idx += written;

        if (i < count - 1) {
            payload[idx++] = ',';
        }
    }

    payload[idx++] = ']';
    payload[idx]   = '\0';

    esp_http_client_config_t config = {
        .url  = CONFIG_SERVER_URL "/data/batch",
        .host = CONFIG_SERVER_ADDRESS,
        .port = 5000,
        .path = "/data/batch",
        .transport_type = HTTP_TRANSPORT_OVER_TCP,
        .method = HTTP_METHOD_POST,
        .auth_type = HTTP_AUTH_TYPE_NONE,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, payload, idx);

    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        int code = esp_http_client_get_status_code(client);
        if (code != 201 && code != 207) {
            ESP_LOGW(TAG, "Server returned HTTP %d", code);
        }
    } else {
        ESP_LOGE(TAG, "POST /data/batch failed: %s", esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
}

/* ── HTTP: poll fan duty ─────────────────────────────────────────────────── */

/*
 * Fetches GET /fan/duty and returns the duty percent (0–100).
 * Returns -1 on any error, in which case the caller keeps the existing duty.
 *
 * Expected response body: {"duty":<integer>}
 * Parsed with strstr() — no JSON library needed for such a small payload.
 */
static int fetch_fan_duty(void)
{
    char rx_buf[HTTP_RX_BUF_SIZE] = {0};

    esp_http_client_config_t config = {
        .url = CONFIG_SERVER_URL "/fan/duty",
        .host = CONFIG_SERVER_ADDRESS,
        .port = 5000,
        .path = "/fan/duty",
        .transport_type = HTTP_TRANSPORT_OVER_TCP,
        .method = HTTP_METHOD_GET,
        .auth_type = HTTP_AUTH_TYPE_NONE,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "GET /fan/duty open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return -1;
    }

    esp_http_client_fetch_headers(client);

    int rx_len = esp_http_client_read(client, rx_buf, sizeof(rx_buf) - 1);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    if (rx_len <= 0) {
        ESP_LOGE(TAG, "GET /fan/duty: empty response");
        return -1;
    }
    rx_buf[rx_len] = '\0';

    char *p = strstr(rx_buf, "\"duty\"");
    if (!p) {
        ESP_LOGE(TAG, "GET /fan/duty: unexpected body: %s", rx_buf);
        return -1;
    }
    p += 6;
    while (*p == ' ' || *p == ':') ++p;

    int duty = atoi(p);
    if (duty < 0)   duty = 0;
    if (duty > 100) duty = 100;
    return duty;
}

/* ── entry point ────────────────────────────────────────────────────────── */

_Noreturn void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_INFO);

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    wifi_init_enterprise();
    sntp_sync();
    fan_init();

    vTaskDelay(pdMS_TO_TICKS(2000));

    /* ── 1-Wire bus init ─────────────────────────────────────────────────── */

    OneWireBus *owb;
    owb_rmt_driver_info rmt_driver_info;
    owb = owb_rmt_initialize(&rmt_driver_info, GPIO_DS18B20,
                             RMT_CHANNEL_1, RMT_CHANNEL_0);
    owb_use_crc(owb, true);

    /* Discover all connected devices. */
    ESP_LOGI(TAG, "Scanning 1-Wire bus for DS18B20 devices...");
    OneWireBus_ROMCode  device_rom_codes[MAX_DEVICES] = {0};
    char                probe_ids[MAX_DEVICES][OWB_ROM_CODE_STRING_LENGTH];
    int                 num_devices = 0;

    OneWireBus_SearchState search_state = {0};
    bool found = false;
    owb_search_first(owb, &search_state, &found);
    while (found && num_devices < MAX_DEVICES) {
        owb_string_from_rom_code(search_state.rom_code,
                                 probe_ids[num_devices],
                                 OWB_ROM_CODE_STRING_LENGTH);
        ESP_LOGI(TAG, "  [%d] %s", num_devices, probe_ids[num_devices]);
        device_rom_codes[num_devices] = search_state.rom_code;
        ++num_devices;
        owb_search_next(owb, &search_state, &found);
    }
    ESP_LOGI(TAG, "Found %d device%s", num_devices, num_devices == 1 ? "" : "s");

    if (num_devices == 0) {
        ESP_LOGE(TAG, "No DS18B20 devices found — check wiring. Halting.");
        while (1) vTaskDelay(pdMS_TO_TICKS(1000));
    }

    /* ── DS18B20 init ────────────────────────────────────────────────────── */

    DS18B20_Info *devices[MAX_DEVICES] = {0};
    for (int i = 0; i < num_devices; ++i) {
        DS18B20_Info *info = ds18b20_malloc();
        devices[i] = info;

        if (num_devices == 1) {
            ds18b20_init_solo(info, owb);
        } else {
            ds18b20_init(info, owb, device_rom_codes[i]);
        }
        ds18b20_use_crc(info, true);
        ds18b20_set_resolution(info, DS18B20_RESOLUTION);
    }

    bool parasitic_power = false;
    ds18b20_check_for_parasite_power(owb, &parasitic_power);
    if (parasitic_power) {
        ESP_LOGW(TAG, "Parasitic-powered devices detected");
    }
    owb_use_parasitic_power(owb, parasitic_power);

#ifdef CONFIG_ENABLE_STRONG_PULLUP_GPIO
    owb_use_strong_pullup_gpio(owb, CONFIG_STRONG_PULLUP_GPIO);
#endif

    /* ── main sampling loop ─────────────────────────────────────────────── */

    int errors_count[MAX_DEVICES] = {0};
    int sample_count = 0;
    int current_duty = 0;
    TickType_t last_wake_time = xTaskGetTickCount();

    while (1) {
        ds18b20_convert_all(owb);
        ds18b20_wait_for_conversion(devices[0]);

        float       readings[MAX_DEVICES] = {0};
        DS18B20_ERROR errors[MAX_DEVICES] = {0};

        for (int i = 0; i < num_devices; ++i) {
            errors[i] = ds18b20_read_temp(devices[i], &readings[i]);
        }

        /* Build list of successful readings for the batch POST. */
        ESP_LOGI(TAG, "Sample %d:", ++sample_count);
        int  good_count = 0;
        float good_readings[MAX_DEVICES];
        char  good_ids[MAX_DEVICES][OWB_ROM_CODE_STRING_LENGTH];

        for (int i = 0; i < num_devices; ++i) {
            if (errors[i] != DS18B20_OK) {
                ++errors_count[i];
                ESP_LOGW(TAG, "  [%d] %s: read error (%d total)",
                         i, probe_ids[i], errors_count[i]);
                continue;
            }
            ESP_LOGI(TAG, "  [%d] %s: %.1f °C", i, probe_ids[i], readings[i]);
            memcpy(good_ids[good_count], probe_ids[i], OWB_ROM_CODE_STRING_LENGTH);
            good_readings[good_count] = readings[i];
            ++good_count;
        }

        if (good_count > 0) {
            post_temperatures(good_count, good_ids, good_readings);
        }

        /* Poll server for fan duty setpoint; apply only on change. */
        int new_duty = fetch_fan_duty();
        if (new_duty >= 0 && new_duty != current_duty) {
            fan_set_duty((uint8_t)new_duty);
            current_duty = new_duty;
        }

        vTaskDelayUntil(&last_wake_time, pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
    }
}
