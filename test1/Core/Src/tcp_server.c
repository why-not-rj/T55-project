/* tcp_server.c - Sends raw encoder position as plain text over TCP port 80 */

#include "tcp_server.h"
#include "lwip/tcp.h"
#include <string.h>
#include <stdio.h>
#include "main.h"

extern volatile int32_t encoder_position;

static err_t tcp_server_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    if (p == NULL) {
        tcp_close(pcb);
        return ERR_OK;
    }

    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);

    /* Build response: just the integer position as plain text */
    char buf[64];
    int len = snprintf(buf, sizeof(buf),
        "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n%ld\r\n",
        (long)encoder_position);

    tcp_write(pcb, buf, len, TCP_WRITE_FLAG_COPY);
    tcp_output(pcb);
    tcp_close(pcb);
    return ERR_OK;
}

static err_t tcp_server_accept(void *arg, struct tcp_pcb *newpcb, err_t err)
{
    if (err != ERR_OK || newpcb == NULL) return ERR_VAL;
    tcp_setprio(newpcb, TCP_PRIO_MIN);
    tcp_recv(newpcb, tcp_server_recv);
    return ERR_OK;
}

void tcp_server_init(void)
{
    struct tcp_pcb *pcb = tcp_new();
    if (pcb == NULL) {
        // Red LED on = tcp_new failed
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_14, GPIO_PIN_SET);
        return;
    }

    err_t err = tcp_bind(pcb, IP_ADDR_ANY, 80);
    if (err != ERR_OK) {
        // Both LEDs = bind failed
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_SET);
        return;
    }

    pcb = tcp_listen(pcb);
    if (pcb == NULL) {
        HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);
        return;
    }

    tcp_accept(pcb, tcp_server_accept);

    // Green LED = success
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_SET);
}
