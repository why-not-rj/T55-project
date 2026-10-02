#ifndef UDP_SERVER_H
#define UDP_SERVER_H

#include <stdint.h>

/* Flash storage and runtime calibration configuration structure */
typedef struct __attribute__((packed)) {
    uint32_t magic;               // Validation key (0xCAL12345)
    float    th_min, th_max;      // Throttle angle range
    float    cl_min, cl_max;      // Clutch angle range
    float    br_hall_min, br_hall_max; // Brake Hall angle range
    int32_t  br_lc_min, br_lc_max;     // Brake Load Cell raw range
    float    br_lc_trust;         // 0.0f (100% Hall) to 1.0f (100% Load Cell)
    int32_t  rs_min, rs_max;      // Right Tiller raw range
    int32_t  ls_min, ls_max;      // Left Tiller raw range
    float    gear_col_left, gear_col_right; // Gearbox gate X boundaries
    int32_t  gear_row_top, gear_row_bot;   // Gearbox gate Y boundaries
} CalibConfig_t;

/* Dynamic Incoming UDP Command Packet from Desktop UI */
typedef struct __attribute__((packed)) {
    uint8_t       cmd_type;  // 0x01 = Live Update (RAM), 0x02 = Save to Flash, 0x03 = Reset Defaults
    CalibConfig_t config;
} CalibCommandPacket;

void udp_server_init(void);
void udp_server_send_position(void);

#endif /* UDP_SERVER_H */
