#ifndef TCP_SERVER_H
#define TCP_SERVER_H

#include "lwip/tcp.h"

/* Initialize and start the TCP HTTP server on port 80 */
void tcp_server_init(void);

#endif /* TCP_SERVER_H */
