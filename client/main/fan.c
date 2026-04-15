#include "fan.h"

#include "driver/ledc.h"
#include "esp_err.h"
#include "esp_log.h"

/*
 * 25 kHz PWM, active-high.
 *
 * Timer resolution: 10-bit (1024 steps).
 * With the 80 MHz APB clock the LEDC fractional divider gives an exact
 * 25 kHz output:  80 000 000 / (3.125 * 1024) = 25 000 Hz
 * ESP-IDF's ledc_timer_config() resolves the divider automatically when
 * freq_hz and duty_resolution are supplied.
 */
#define FAN_SPEED_MODE   LEDC_LOW_SPEED_MODE
#define FAN_TIMER        LEDC_TIMER_0
#define FAN_CHANNEL      LEDC_CHANNEL_0
#define FAN_FREQ_HZ      25000
#define FAN_RESOLUTION   LEDC_TIMER_10_BIT
#define FAN_DUTY_MAX     ((1u << 10) - 1)   /* 1023 for 10-bit */

static const char *TAG = "fan";

void fan_init(void)
{
    ledc_timer_config_t timer = {
        .speed_mode      = FAN_SPEED_MODE,
        .duty_resolution = FAN_RESOLUTION,
        .timer_num       = FAN_TIMER,
        .freq_hz         = FAN_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));

    ledc_channel_config_t channel = {
        .speed_mode = FAN_SPEED_MODE,
        .channel    = FAN_CHANNEL,
        .timer_sel  = FAN_TIMER,
        .intr_type  = LEDC_INTR_DISABLE,
        .gpio_num   = CONFIG_FAN_PWM_GPIO,
        .duty       = 0,
        .hpoint     = 0,   /* rising edge at the start of each period */
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel));

    ESP_LOGI(TAG, "PWM fan initialised: GPIO %d, %d Hz, %d-bit resolution",
             CONFIG_FAN_PWM_GPIO, FAN_FREQ_HZ, 10);
}

void fan_set_duty(uint8_t duty_percent)
{
    if (duty_percent > 100) {
        duty_percent = 100;
    }

    uint32_t duty_raw = (duty_percent * FAN_DUTY_MAX + 50) / 100;  /* rounded */

    ESP_ERROR_CHECK(ledc_set_duty(FAN_SPEED_MODE, FAN_CHANNEL, duty_raw));
    ESP_ERROR_CHECK(ledc_update_duty(FAN_SPEED_MODE, FAN_CHANNEL));

    ESP_LOGI(TAG, "Duty set to %d %% (raw %lu / %d)", duty_percent, duty_raw, FAN_DUTY_MAX);
}
