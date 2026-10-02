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
