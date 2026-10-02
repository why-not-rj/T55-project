#include "udp_server.h"
#include "lwip/udp.h"
#include "lwip/ip_addr.h"
#include <string.h>

extern volatile float right_stick_scaled;
extern volatile float left_stick_scaled;
extern volatile float accelerator_scaled;
extern volatile float brake_scaled;
extern volatile float clutch_scaled;
extern volatile int32_t current_gear;
extern volatile int32_t encoder_position;   // Left  raw (TIM2) -- ADD THIS
extern volatile int32_t encoder2_position;  // Right raw (TIM3) -- ADD THIS
extern volatile uint8_t button_state;
extern float throttle_angle;
extern float clutch_angle;
extern float brake_hall_angle;
extern int32_t loadcell_filtered;
extern uint32_t H1_ADC;
extern uint32_t H2_ADC;
extern int32_t h_fused;
extern float angle_deg;

extern CalibConfig_t g_calib;
extern void save_calib_to_flash(void);
extern void load_calib_defaults(void);

static struct udp_pcb *upcb;
static ip_addr_t dest_addr;
static uint16_t dest_port = 6969;
static uint8_t  dest_known = 0;

static void udp_server_recv(void *arg, struct udp_pcb *pcb, struct pbuf *p,
                             const ip_addr_t *addr, u16_t port)
{
    if (p == NULL) return;
    dest_addr  = *addr;
    dest_port  = port;
    dest_known = 1;
    if (p->len == sizeof(CalibCommandPacket)) {
            CalibCommandPacket *cmd = (CalibCommandPacket*)p->payload;

            if (cmd->cmd_type == 0x01) {
                /* 1. Live Update (RAM only) */
                g_calib = cmd->config;
            }
            else if (cmd->cmd_type == 0x02) {
                /* 2. Save current RAM configuration to STM32 Flash */
                g_calib = cmd->config;
                save_calib_to_flash();
            }
            else if (cmd->cmd_type == 0x03) {
                /* 3. Reset to Hardcoded Factory Defaults */
                load_calib_defaults();
                save_calib_to_flash();
            }
        }
    pbuf_free(p);
}

#pragma pack(push, 1)
typedef struct {
    float   right_stick;
    float   left_stick;
    float   accelerator;
    float   brake;
    float   clutch;
    int32_t gear;
    int32_t right_stick_raw;
    int32_t left_stick_raw;
    int32_t button;
    float   th_angle_raw;
    float   cl_angle_raw;
    float   br_angle_raw;
    int32_t br_lc_raw;
    uint32_t h1_adc;
    uint32_t h2_adc;
    int32_t  h_fused;
    float    gear_angle_raw;
} PedalPacket;
#pragma pack(pop)

void udp_server_send_position(void)
{
    if (!dest_known) return;

    PedalPacket pkt;
    pkt.right_stick = right_stick_scaled;
    pkt.left_stick  = left_stick_scaled;
    pkt.accelerator = accelerator_scaled;
    pkt.brake       = brake_scaled;
    pkt.clutch      = clutch_scaled;
    pkt.gear        = current_gear;
    pkt.right_stick_raw = encoder2_position;  // TIM3 = Right
    pkt.left_stick_raw  = encoder_position;   // TIM2 = Left
    pkt.button          = button_state;

    pkt.th_angle_raw   = throttle_angle;
    pkt.cl_angle_raw   = clutch_angle;
    pkt.br_angle_raw   = brake_hall_angle;
    pkt.br_lc_raw      = loadcell_filtered;
    pkt.h1_adc         = H1_ADC;
    pkt.h2_adc         = H2_ADC;
    pkt.h_fused        = h_fused;
    pkt.gear_angle_raw = angle_deg;


    struct pbuf *p = pbuf_alloc(PBUF_TRANSPORT, sizeof(pkt), PBUF_RAM);
    if (p == NULL) return;

    memcpy(p->payload, &pkt, sizeof(pkt));
    udp_sendto(upcb, p, &dest_addr, dest_port);
    pbuf_free(p);
}

void udp_server_init(void)
{
    upcb = udp_new();
    if (upcb == NULL) return;

    udp_bind(upcb, IP_ADDR_ANY, 6969);
    udp_recv(upcb, udp_server_recv, NULL);
}
