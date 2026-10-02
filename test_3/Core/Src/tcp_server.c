#include "tcp_server.h"
#include "lwip/tcp.h"
#include <string.h>

extern volatile int32_t encoder_position;
extern volatile int32_t pedal_position;

static int int_to_str(int32_t val, char *buf)
{
    int i = 0, neg = 0;
    if (val < 0) { neg = 1; val = -val; }
    if (val == 0) { buf[i++] = '0'; }
    else {
        while (val > 0) { buf[i++] = '0' + (val % 10); val /= 10; }
        if (neg) buf[i++] = '-';
        for (int a=0, b=i-1; a<b; a++,b--) { char t=buf[a]; buf[a]=buf[b]; buf[b]=t; }
    }
    buf[i++] = '\n';  /* newline as delimiter */
    buf[i]   = 0;
    return i;
}

/* Active client — only one at a time */
static struct tcp_pcb *active_client = NULL;

/* Called from main loop to push latest value to connected client */
void tcp_server_send_position(void)
{
    if (active_client == NULL) return;

    char buf[32];
    int len = 0;
    len += int_to_str(encoder_position, buf + len);
    buf[len-1] = ',';
    len += int_to_str(pedal_position, buf + len);

    err_t err = tcp_write(active_client, buf, len, TCP_WRITE_FLAG_COPY);
    if (err == ERR_OK)
        tcp_output(active_client);
    else {
        tcp_close(active_client);
        active_client = NULL;
    }
}

static err_t tcp_server_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    if (p == NULL) {
        /* Client disconnected */
        active_client = NULL;
        tcp_close(pcb);
        return ERR_OK;
    }
    tcp_recved(pcb, p->tot_len);
    pbuf_free(p);
    return ERR_OK;
}

static err_t tcp_server_accept(void *arg, struct tcp_pcb *newpcb, err_t err)
{
    if (err != ERR_OK || newpcb == NULL) return ERR_VAL;

    /* Close previous client if any */
    if (active_client != NULL) {
        tcp_close(active_client);
        active_client = NULL;
    }

    active_client = newpcb;
    tcp_setprio(newpcb, TCP_PRIO_MIN);
    tcp_recv(newpcb, tcp_server_recv);

    return ERR_OK;
}

void tcp_server_init(void)
{
    struct tcp_pcb *pcb = tcp_new();
    if (pcb == NULL) return;
    tcp_bind(pcb, IP_ADDR_ANY, 23);
    pcb = tcp_listen(pcb);
    if (pcb == NULL) return;
    tcp_accept(pcb, tcp_server_accept);
}
