#ifndef TCP_SERVER_H
#define TCP_SERVER_H

#include "lwip/tcp.h"

void tcp_server_init(void);
void tcp_server_send_position(void);   /* call this from main loop */

#endif
