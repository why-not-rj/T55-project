import hid

VENDOR_ID = 13422
PRODUCT_ID = 3

h = hid.device()
h.open(VENDOR_ID, PRODUCT_ID)
h.set_nonblocking(1)

while True:
    data = h.read(64)
    if data:
        print(data)