#include "wifi.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_eap_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"

#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1
#define MAX_RECONNECT_TRIES 10

static const char *TAG = "wifi";

static EventGroupHandle_t s_wifi_event_group;
static int s_retry_count = 0;

/* ── event handler ─────────────────────────────────────────────────────── */

static void event_handler(void *arg, esp_event_base_t event_base,
                           int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();

    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_count < MAX_RECONNECT_TRIES) {
            s_retry_count++;
            ESP_LOGW(TAG, "Disconnected — retrying (%d/%d)...",
                     s_retry_count, MAX_RECONNECT_TRIES);
            esp_wifi_connect();
        } else {
            ESP_LOGE(TAG, "Could not connect after %d attempts", MAX_RECONNECT_TRIES);
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }

    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP address: " IPSTR, IP2STR(&event->ip_info.ip));
        s_retry_count = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

/* ── public API ─────────────────────────────────────────────────────────── */

void wifi_init_enterprise(void)
{
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    /* Register for both WiFi and IP events. */
    esp_event_handler_instance_t h_wifi, h_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
            WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, &h_wifi));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
            IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, &h_ip));

    /* Basic station config — only the SSID is needed; EAP handles auth. */
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_WIFI_SSID,
            /* threshold.authmode is intentionally left at default (OPEN) so
             * the WPA2-Enterprise stack can negotiate the EAP exchange.    */
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));

    /*
     * WPA2-Enterprise (802.1X / EAP-PEAP) credentials.
     *
     * identity : outer/anonymous identity — sent in the clear.
     * username : inner identity — sent encrypted inside the EAP tunnel.
     * password : inner password.
     *
     * For this demo, CA certificate validation is skipped. In production,
     * supply the RADIUS server's CA cert via esp_eap_client_set_ca_cert().
     */
    ESP_ERROR_CHECK(esp_eap_client_set_identity(
            (const uint8_t *)CONFIG_EAP_IDENTITY, strlen(CONFIG_EAP_IDENTITY)));
    ESP_ERROR_CHECK(esp_eap_client_set_username(
            (const uint8_t *)CONFIG_EAP_USERNAME, strlen(CONFIG_EAP_USERNAME)));
    ESP_ERROR_CHECK(esp_eap_client_set_password(
            (const uint8_t *)CONFIG_EAP_PASSWORD, strlen(CONFIG_EAP_PASSWORD)));

    /* Enable WPA2-Enterprise on the station interface. */
    ESP_ERROR_CHECK(esp_wifi_sta_enterprise_enable());

    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_LOGI(TAG, "Connecting to \"%s\" (WPA2-Enterprise / EAP-PEAP)...",
             CONFIG_WIFI_SSID);

    /* Block until connected or permanently failed. */
    EventBits_t bits = xEventGroupWaitBits(
            s_wifi_event_group,
            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
            pdFALSE, pdFALSE,
            portMAX_DELAY);

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "WiFi connected successfully");
    } else {
        ESP_LOGE(TAG, "WiFi connection failed permanently — halting");
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}
