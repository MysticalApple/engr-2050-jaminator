#pragma once

/**
 * @brief Initialise WiFi in station mode and connect using WPA2-Enterprise
 *        (EAP-PEAP / MSCHAPv2).
 *
 * Credentials and SSID are taken from Kconfig (menuconfig).
 * This function blocks until an IP address is obtained or a fatal error
 * occurs. On failure it logs an error and spins forever — suitable for a
 * demo where there is nothing useful to do without a network.
 */
void wifi_init_enterprise(void);
