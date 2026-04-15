#pragma once

#include <stdint.h>

/**
 * @brief Initialise the LEDC peripheral for 25 kHz PWM fan control.
 *
 * Configures LEDC timer 0 and channel 0 on CONFIG_FAN_PWM_GPIO.
 * Output is active-high; duty cycle starts at 0 % (fan off).
 * Must be called once before fan_set_duty().
 */
void fan_init(void);

/**
 * @brief Set the fan duty cycle.
 *
 * @param duty_percent  Desired duty cycle, 0–100 (clamped if out of range).
 */
void fan_set_duty(uint8_t duty_percent);
