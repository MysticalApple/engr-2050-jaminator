/*
 * Temperature Reporter — ESP-IDF v5.0
 *
 * Reads temperatures from DS18B20 probes on a 1-Wire bus every second and
 * POSTs each reading as JSON to the Flask monitoring server.
 *
 * POST /data   { "probe_id": "<ROM>", "temperature": <float>, "timestamp": "<ISO-8601>" }
 *
 * Configure SSID, EAP credentials, server URL, and GPIO via menuconfig.
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

/* ── configuration ──────────────────────────────────────────────────────── */

#define GPIO_DS18B20        (CONFIG_ONE_WIRE_GPIO)
#define MAX_DEVICES         (8)
#define DS18B20_RESOLUTION  (DS18B20_RESOLUTION_12_BIT)
#define SAMPLE_PERIOD_MS    (1000)

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

/**
 * @brief Write current UTC time as ISO-8601 into buf.
 * @return true if the clock is synchronised, false otherwise.
 */
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

/* ── HTTP helper ────────────────────────────────────────────────────────── */

/**
 * @brief POST a single temperature reading to the server.
 *
 * @param probe_id   ROM code string identifying the probe.
 * @param temperature Temperature in degrees Celsius.
 */
static void post_temperature(const char *probe_id, float temperature)
{
    char timestamp[32];
    char payload[160];

    if (get_timestamp(timestamp, sizeof(timestamp))) {
        snprintf(payload, sizeof(payload),
                 "{\"probe_id\":\"%s\",\"temperature\":%.2f,\"timestamp\":\"%s\"}",
                 probe_id, temperature, timestamp);
    } else {
        /* Omit timestamp — the server will use its own clock. */
        snprintf(payload, sizeof(payload),
                 "{\"probe_id\":\"%s\",\"temperature\":%.2f}",
                 probe_id, temperature);
    }

    esp_http_client_config_t config = {
        .url    = "http://" CONFIG_SERVER_ADDRESS ":5000/data",
        .host = CONFIG_SERVER_ADDRESS,
        .port = 5000,
        .path = "/data",
        .transport_type = HTTP_TRANSPORT_OVER_TCP,
        .method = HTTP_METHOD_POST,
        .auth_type = HTTP_AUTH_TYPE_NONE,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, payload, (int)strlen(payload));

    ESP_LOGI(TAG, "Performing...");
    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        int code = esp_http_client_get_status_code(client);
        if (code != 201) {
            ESP_LOGW(TAG, "Server returned HTTP %d for probe %s", code, probe_id);
        }
    } else {
        ESP_LOGE(TAG, "POST failed for %s: %s", probe_id, esp_err_to_name(err));
    }

    esp_http_client_cleanup(client);
}

/**
 * @brief POST multiple temperature readings to the server.
 *
 * @param count         Number of measurements to send
 * @param probe_ids     ROM code strings identifying the probes.
 * @param temperatures  Temperatures in degrees Celsius.
 */
static void post_temperature_batch(int count, const char probe_ids[][OWB_ROM_CODE_STRING_LENGTH], float *temperatures)
{
    char timestamp[32];
    int payload_index = 0;
    char payload[512];

    payload[payload_index] = '[';
    payload_index++;
    for (int i = 0; i < count; i++) {
        if (get_timestamp(timestamp, sizeof(timestamp))) {
            int ret = snprintf(payload + payload_index,
                                      sizeof(payload) - payload_index,
                                      "{\"probe_id\":\"%s\",\"temperature\":%."
                                      "2f,\"timestamp\":\"%s\"}",
                                      probe_ids[i], temperatures[i], timestamp);
            payload_index += ret;
        } else {
            payload_index += snprintf(payload + payload_index,
                                      sizeof(payload) - payload_index,
                                      "{\"probe_id\":\"%s\",\"temperature\":%."
                                      "2f}",
                                      probe_ids[i], temperatures[i]);
        }

        if (i < count - 1) {
            payload[payload_index] = ',';
            payload_index++;
        }
    }
    payload[payload_index] = ']';
    payload_index++;

    if (payload_index >= sizeof(payload)) {
        ESP_LOGE(TAG, "Buffer overflow in post_temperature: payload_index=%d, payload=%s", payload_index, payload);
    }

    esp_http_client_config_t config = {
        .url    = "http://" CONFIG_SERVER_ADDRESS ":5000/data/batch",
        .host = CONFIG_SERVER_ADDRESS,
        .port = 5000,
        .path = "/data/batch",
        .transport_type = HTTP_TRANSPORT_OVER_TCP,
        .method = HTTP_METHOD_POST,
        .auth_type = HTTP_AUTH_TYPE_NONE,
    };

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, payload, payload_index);

    ESP_LOGI(TAG, "Performing...");
    esp_err_t err = esp_http_client_perform(client);
    if (err == ESP_OK) {
        int code = esp_http_client_get_status_code(client);
        if (code != 201) {
            ESP_LOGW(TAG, "Server returned HTTP %d at %s", code, timestamp);
        }
    } else {
        ESP_LOGE(TAG, "POST failed at %s: %s", timestamp, esp_err_to_name(err));
    }

    esp_http_client_cleanup(client);
}

/* ── entry point ────────────────────────────────────────────────────────── */

_Noreturn void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_INFO);

    /* NVS is required by the WiFi driver. */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* Connect to the WPA2-Enterprise network. Blocks until IP is obtained. */
    wifi_init_enterprise();

    /* Sync wall-clock time so we can attach ISO-8601 timestamps. */
    sntp_sync();

    /* Short settle time before 1-Wire communication (mirrors the example). */
    vTaskDelay(pdMS_TO_TICKS(2000));

    /* ── 1-Wire bus initialisation ─────────────────────────────────────── */

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

    /* ── DS18B20 device initialisation ────────────────────────────────── */

    DS18B20_Info *devices[MAX_DEVICES] = {0};
    for (int i = 0; i < num_devices; ++i) {
        DS18B20_Info *info = ds18b20_malloc();
        devices[i] = info;

        if (num_devices == 1) {
            ds18b20_init_solo(info, owb);       /* skip ROM matching when alone */
        } else {
            ds18b20_init(info, owb, device_rom_codes[i]);
        }
        ds18b20_use_crc(info, true);
        ds18b20_set_resolution(info, DS18B20_RESOLUTION);
    }

    /* Detect parasitic-powered sensors (mirrors the example). */
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
    TickType_t last_wake_time = xTaskGetTickCount();

    while (1) {
        /*
         * Start conversion on every device simultaneously, then wait.
         * This mirrors the efficient approach in the example: read all
         * temperatures before doing anything that might take time (like
         * logging or network I/O).
         */
        ds18b20_convert_all(owb);
        ds18b20_wait_for_conversion(devices[0]);

        float readings[MAX_DEVICES]      = {0};
        DS18B20_ERROR errors[MAX_DEVICES] = {0};

        for (int i = 0; i < num_devices; ++i) {
            errors[i] = ds18b20_read_temp(devices[i], &readings[i]);
        }

        /* Now log and POST — order no longer matters for accuracy. */
        ESP_LOGI(TAG, "Sample %d:", ++sample_count);
        for (int i = 0; i < num_devices; ++i) {
            if (errors[i] != DS18B20_OK) {
                ++errors_count[i];
                ESP_LOGW(TAG, "  [%d] %s: read error (%d total)",
                         i, probe_ids[i], errors_count[i]);
                continue;
            }
            ESP_LOGI(TAG, "  [%d] %s: %.1f °C", i, probe_ids[i], readings[i]);
        }
        post_temperature_batch(num_devices, probe_ids, readings);

        /* Sleep for the remainder of the 1-second period. */
        vTaskDelayUntil(&last_wake_time, pdMS_TO_TICKS(SAMPLE_PERIOD_MS));
    }
}
