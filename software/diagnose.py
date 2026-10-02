import socket
import time

BOARD_IP = "192.168.1.100"
BOARD_PORT = 6969

# Create socket
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)

print(f"[*] Sending ping packet to {BOARD_IP}:{BOARD_PORT}...")
# Send 1-byte handshake to force dest_known = 1 in udp_server.c
sock.sendto(b'\x01', (BOARD_IP, BOARD_PORT))

print("[*] Waiting for reply from STM32...")
try:
    data, addr = sock.recvfrom(2048)
    print(f"[SUCCESS] Received {len(data)} bytes from {addr}!")
    print(f"Raw Hex: {data.hex()[:40]}...")
except socket.timeout:
    print("[ERROR] Timed out! No response received from STM32.")
except Exception as e:
    print(f"[ERROR] Socket error: {e}")