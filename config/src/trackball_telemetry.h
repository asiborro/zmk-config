/* SPDX-License-Identifier: MIT */
#pragma once
#include <stdint.h>
#define KEYBOARD_TRACKBALL_PACKET_SIZE 20
void keyboard_trackball_packet(uint8_t packet[KEYBOARD_TRACKBALL_PACKET_SIZE]);
