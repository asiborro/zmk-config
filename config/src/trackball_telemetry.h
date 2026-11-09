/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#ifdef CONFIG_PAW3395
#define KEYBOARD_TRACKBALL_PACKET_SIZE 24
#else
#define KEYBOARD_TRACKBALL_PACKET_SIZE 20
#endif
void keyboard_trackball_packet(uint8_t packet[KEYBOARD_TRACKBALL_PACKET_SIZE]);
