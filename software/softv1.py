import sys
import socket
import struct
import time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QProgressBar, QPushButton, 
                             QSlider, QGroupBox, QGridLayout, QTabWidget)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

BOARD_IP = "192.168.1.100"
BOARD_PORT = 6969

class ZeroLatencyUDPListener(QThread):
    telemetry_signal = pyqtSignal(dict)
    connection_status = pyqtSignal(bool)

    def __init__(self, shared_sock):
        super().__init__()
        self.sock = shared_sock

    def run(self):
        self.sock.settimeout(0.05)
        
        handshake_payload = b'\x01'
        last_ping = 0
        last_packet_time = 0
        connected = False

        while True:
            now = time.time()

            # 1. Periodically ping the STM32 board every 1.0s to update dest_port on STM32
            if now - last_ping > 1.0:
                try:
                    self.sock.sendto(handshake_payload, (BOARD_IP, BOARD_PORT))
                except Exception as e:
                    print(f"UDP Ping Error: {e}")
                last_ping = now

            # 2. Drain buffer to read the latest frame
            latest_data = None
            while True:
                try:
                    data, addr = self.sock.recvfrom(2048)
                    latest_data = data
                except (socket.timeout, BlockingIOError):
                    break
                except Exception as e:
                    print(f"Recv Error: {e}")
                    break

            # 3. Process packet
            if latest_data is not None:
                last_packet_time = now
                data_len = len(latest_data)

                if not connected:
                    connected = True
                    self.connection_status.emit(True)

                try:
                    if data_len >= 68:
                        # Full extended telemetry packet (68 - 72 bytes)
                        unpacked = struct.unpack('<fffffiiiifffiIIif', latest_data[:68])
                        pkt = {
                            'r_stick': unpacked[0], 'l_stick': unpacked[1],
                            'accel': unpacked[2], 'brake': unpacked[3], 'clutch': unpacked[4],
                            'gear': unpacked[5], 'r_raw': unpacked[6], 'l_raw': unpacked[7], 'btn': unpacked[8],
                            'th_angle_raw': unpacked[9], 'cl_angle_raw': unpacked[10], 'br_angle_raw': unpacked[11],
                            'br_lc_raw': unpacked[12], 'h1_adc': unpacked[13], 'h2_adc': unpacked[14],
                            'h_fused': unpacked[15], 'gear_angle_raw': unpacked[16]
                        }
                    elif data_len >= 24:
                        # Fallback standard telemetry packet
                        unpacked = struct.unpack('<fffffi', latest_data[:24])
                        pkt = {
                            'r_stick': unpacked[0], 'l_stick': unpacked[1],
                            'accel': unpacked[2], 'brake': unpacked[3], 'clutch': unpacked[4],
                            'gear': unpacked[5], 'r_raw': 0, 'l_raw': 0, 'btn': 0,
                            'th_angle_raw': 0.0, 'cl_angle_raw': 0.0, 'br_angle_raw': 0.0,
                            'br_lc_raw': 0, 'h1_adc': 0, 'h2_adc': 0,
                            'h_fused': 0, 'gear_angle_raw': 0.0
                        }
                    else:
                        pkt = None

                    if pkt is not None:
                        self.telemetry_signal.emit(pkt)

                except Exception as e:
                    print(f"Unpack Error ({data_len} bytes): {e}")

            elif connected and (now - last_packet_time > 2.5):
                connected = False
                self.connection_status.emit(False)

            time.sleep(0.005)


class ProductionCalibrationUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sim Rig Master Calibration Suite")
        self.setGeometry(50, 50, 1050, 750)
        
        self.calib = {
            'th_min': -138.23, 'th_max': -118.54,
            'cl_min': -5.83, 'cl_max': 13.16,
            'br_hall_min': -133.11, 'br_hall_max': -121.36,
            'br_lc_min': -69500, 'br_lc_max': 230000,
            'br_lc_trust': 0.5,
            'rs_min': 0, 'rs_max': -475,
            'ls_min': 0, 'ls_max': 490,
            'gear_col_left': -58.0, 'gear_col_right': -48.0,
            'gear_row_top': -1500, 'gear_row_bot': 1500
        }
        
        self.latest_pkt = None
        self.gear_captures = {}

        # SINGLE SHARED UDP SOCKET
        self.shared_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.init_ui()
        
        self.listener = ZeroLatencyUDPListener(self.shared_sock)
        self.listener.telemetry_signal.connect(self.update_telemetry)
        self.listener.connection_status.connect(self.update_status)
        self.listener.start()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        self.status_bar = QLabel("STATUS: INITIALIZING CONNECTION...")
        self.status_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_bar.setStyleSheet("background-color: #333; color: orange; font-size: 14px; font-weight: bold; padding: 6px;")
        main_layout.addWidget(self.status_bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.create_pedals_tab(), "Pedals (Moza & Loadcell)")
        self.tabs.addTab(self.create_gearbox_tab(), "H-Pattern Gearbox (H1/H2 & TLE)")
        self.tabs.addTab(self.create_tillers_tab(), "Tillers (Quadrature Encoders)")
        main_layout.addWidget(self.tabs)

        cmd_layout = QHBoxLayout()
        save_btn = QPushButton("FLASH WRITE (PERSIST TO EEPROM)")
        save_btn.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; font-size: 14px; padding: 12px;")
        save_btn.clicked.connect(self.save_to_flash)

        reset_btn = QPushButton("RESET DEFAULTS")
        reset_btn.setStyleSheet("background-color: #c62828; color: white; font-weight: bold; font-size: 14px; padding: 12px;")
        reset_btn.clicked.connect(self.reset_defaults)

        cmd_layout.addWidget(save_btn, 2)
        cmd_layout.addWidget(reset_btn, 1)
        main_layout.addLayout(cmd_layout)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    # ------------------- TAB 1: PEDALS -------------------
    def create_pedals_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        self.pedal_bars = {}
        self.pedal_raw_labels = {}

        for pedal, name in [('accel', 'Throttle'), ('clutch', 'Clutch'), ('brake', 'Brake Hybrid')]:
            box = QGroupBox(f"{name} Control")
            vbox = QVBoxLayout()
            
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)
            
            raw_lbl = QLabel("Raw Input: --")
            vbox.addWidget(raw_lbl)

            btn_hbox = QHBoxLayout()
            btn_min = QPushButton(f"Set {name} Min (Idle)")
            btn_max = QPushButton(f"Set {name} Max (Depressed)")
            
            btn_min.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'min'))
            btn_max.clicked.connect(lambda _, p=pedal: self.capture_pedal_limit(p, 'max'))

            btn_hbox.addWidget(btn_min)
            btn_hbox.addWidget(btn_max)
            vbox.addLayout(btn_hbox)

            box.setLayout(vbox)
            layout.addWidget(box)
            
            self.pedal_bars[pedal] = pbar
            self.pedal_raw_labels[pedal] = raw_lbl

        blend_box = QGroupBox("Brake Load Cell Trust Blender")
        bv = QVBoxLayout()
        self.trust_lbl = QLabel("Blend: 50% Hall Angle / 50% Load Cell Pressure")
        self.trust_slider = QSlider(Qt.Orientation.Horizontal)
        self.trust_slider.setRange(0, 100)
        self.trust_slider.setValue(50)
        self.trust_slider.valueChanged.connect(self.on_trust_slider_change)
        bv.addWidget(self.trust_lbl)
        bv.addWidget(self.trust_slider)
        blend_box.setLayout(bv)
        layout.addWidget(blend_box)

        tab.setLayout(layout)
        return tab

    # ------------------- TAB 2: GEARBOX -------------------
    def create_gearbox_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        live_telemetry_box = QGroupBox("Live Gearbox Telemetry")
        lt_layout = QHBoxLayout()
        self.lbl_h1 = QLabel("H1 ADC: --")
        self.lbl_h2 = QLabel("H2 ADC: --")
        self.lbl_hfused = QLabel("H_Fused: --")
        self.lbl_gangle = QLabel("TLE Angle: --")
        self.lbl_active_gear = QLabel("Active Gear: NEUTRAL")
        self.lbl_active_gear.setStyleSheet("font-size: 18px; font-weight: bold; color: cyan;")

        for w in [self.lbl_h1, self.lbl_h2, self.lbl_hfused, self.lbl_gangle, self.lbl_active_gear]:
            lt_layout.addWidget(w)
        live_telemetry_box.setLayout(lt_layout)
        layout.addWidget(live_telemetry_box)

        grid = QGridLayout()
        self.gear_cards = {}
        gear_names = {1: "Gear 1", 2: "Gear 2", 3: "Gear 3", 4: "Gear 4", 5: "Gear 5", 6: "Gear 6"}
        
        for g_num, name in gear_names.items():
            card = QGroupBox(name)
            card_vbox = QVBoxLayout()
            
            lbl_info = QLabel("H_Fused: -- | Angle: --")
            btn_cap = QPushButton(f"Capture {name} Position")
            btn_cap.clicked.connect(lambda _, g=g_num: self.capture_gear_slot(g))

            card_vbox.addWidget(lbl_info)
            card_vbox.addWidget(btn_cap)
            card.setLayout(card_vbox)

            row = 0 if g_num in [1, 3, 5] else 1
            col = (g_num - 1) // 2
            grid.addWidget(card, row, col)

            self.gear_cards[g_num] = {'box': card, 'lbl': lbl_info}

        layout.addLayout(grid)
        tab.setLayout(layout)
        return tab

    # ------------------- TAB 3: TILLERS -------------------
    def create_tillers_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()

        self.tiller_bars = {}
        self.tiller_raw_lbls = {}

        for stick, name in [('l_stick', 'Left Tiller (TIM2)'), ('r_stick', 'Right Tiller (TIM3)')]:
            box = QGroupBox(name)
            vbox = QVBoxLayout()

            pbar = QProgressBar()
            pbar.setRange(0, 100)
            vbox.addWidget(pbar)

            lbl_raw = QLabel("Raw Counter: 0")
            vbox.addWidget(lbl_raw)

            btn_hbox = QHBoxLayout()
            btn_min = QPushButton("Set Full Left")
            btn_max = QPushButton("Set Full Right")

            btn_min.clicked.connect(lambda _, s=stick: self.capture_tiller_limit(s, 'min'))
            btn_max.clicked.connect(lambda _, s=stick: self.capture_tiller_limit(s, 'max'))

            btn_hbox.addWidget(btn_min)
            btn_hbox.addWidget(btn_max)
            vbox.addLayout(btn_hbox)

            box.setLayout(vbox)
            layout.addWidget(box)

            self.tiller_bars[stick] = pbar
            self.tiller_raw_lbls[stick] = lbl_raw

        tab.setLayout(layout)
        return tab

    # ------------------- TELEMETRY UPDATES -------------------
    def update_status(self, connected):
        if connected:
            self.status_bar.setText("STATUS: ONLINE (100Hz Dynamic Stream - Zero Latency)")
            self.status_bar.setStyleSheet("background-color: #2e7d32; color: white; font-size: 14px; font-weight: bold; padding: 6px;")
        else:
            self.status_bar.setText("STATUS: OFFLINE (Check Power / Ethernet Link)")
            self.status_bar.setStyleSheet("background-color: #c62828; color: white; font-size: 14px; font-weight: bold; padding: 6px;")

    def update_telemetry(self, pkt):
        self.latest_pkt = pkt

        self.pedal_bars['accel'].setValue(max(0, min(100, int(pkt['accel'] * 100))))
        self.pedal_bars['clutch'].setValue(max(0, min(100, int(pkt['clutch'] * 100))))
        self.pedal_bars['brake'].setValue(max(0, min(100, int(pkt['brake'] * 100))))

        self.pedal_raw_labels['accel'].setText(f"TLE Angle Raw: {pkt['th_angle_raw']:.2f}°")
        self.pedal_raw_labels['clutch'].setText(f"TLE Angle Raw: {pkt['cl_angle_raw']:.2f}°")
        self.pedal_raw_labels['brake'].setText(f"Hall: {pkt['br_angle_raw']:.2f}° | LoadCell Raw: {pkt['br_lc_raw']}")

        self.lbl_h1.setText(f"H1 ADC: {pkt['h1_adc']}")
        self.lbl_h2.setText(f"H2 ADC: {pkt['h2_adc']}")
        self.lbl_hfused.setText(f"H_Fused: {pkt['h_fused']}")
        self.lbl_gangle.setText(f"TLE Angle: {pkt['gear_angle_raw']:.2f}°")
        
        g = pkt['gear']
        self.lbl_active_gear.setText(f"Active Gear: GEAR {g}" if g > 0 else "Active Gear: NEUTRAL")

        self.tiller_bars['l_stick'].setValue(max(0, min(100, int(pkt['l_stick'] * 100))))
        self.tiller_bars['r_stick'].setValue(max(0, min(100, int(pkt['r_stick'] * 100))))
        self.tiller_raw_lbls['l_stick'].setText(f"Encoder Counter (TIM2): {pkt['l_raw']}")
        self.tiller_raw_lbls['r_stick'].setText(f"Encoder Counter (TIM3): {pkt['r_raw']}")

    def capture_pedal_limit(self, pedal, limit_type):
        if not self.latest_pkt: return
        
        if pedal == 'accel':
            val = self.latest_pkt['th_angle_raw']
            key = 'th_min' if limit_type == 'min' else 'th_max'
        elif pedal == 'clutch':
            val = self.latest_pkt['cl_angle_raw']
            key = 'cl_min' if limit_type == 'min' else 'cl_max'
        elif pedal == 'brake':
            val_h = self.latest_pkt['br_angle_raw']
            val_lc = self.latest_pkt['br_lc_raw']
            if limit_type == 'min':
                self.calib['br_hall_min'] = val_h
                self.calib['br_lc_min'] = val_lc
            else:
                self.calib['br_hall_max'] = val_h
                self.calib['br_lc_max'] = val_lc
            self.send_calib_packet(0x01)
            return

        self.calib[key] = val
        self.send_calib_packet(0x01)

    def capture_tiller_limit(self, stick, limit_type):
        if not self.latest_pkt: return
        raw = self.latest_pkt['l_raw'] if stick == 'l_stick' else self.latest_pkt['r_raw']
        key = f"ls_{limit_type}" if stick == 'l_stick' else f"rs_{limit_type}"
        self.calib[key] = raw
        self.send_calib_packet(0x01)

    def capture_gear_slot(self, gear_num):
        if not self.latest_pkt: return
        
        hfused = self.latest_pkt['h_fused']
        angle = self.latest_pkt['gear_angle_raw']
        
        self.gear_captures[gear_num] = {'hfused': hfused, 'angle': angle}
        self.gear_cards[gear_num]['lbl'].setText(f"Captured: H_Fused={hfused} | Angle={angle:.2f}°")

        angles = [cap['angle'] for cap in self.gear_captures.values()]
        hfused_vals = [cap['hfused'] for cap in self.gear_captures.values()]

        if len(angles) > 1:
            self.calib['gear_col_left'] = min(angles) + (max(angles) - min(angles)) * 0.33
            self.calib['gear_col_right'] = min(angles) + (max(angles) - min(angles)) * 0.66
        if len(hfused_vals) > 1:
            self.calib['gear_row_top'] = int(min(hfused_vals) / 2)
            self.calib['gear_row_bot'] = int(max(hfused_vals) / 2)

        self.send_calib_packet(0x01)

    def on_trust_slider_change(self, val):
        self.calib['br_lc_trust'] = val / 100.0
        self.trust_lbl.setText(f"Blend: {100-val}% Hall Angle / {val}% Load Cell Pressure")
        self.send_calib_packet(0x01)

    def save_to_flash(self):
        self.send_calib_packet(0x02)

    def reset_defaults(self):
        self.send_calib_packet(0x03)

    def send_calib_packet(self, cmd_type):
        packed = struct.pack(
            '<BIffffffiifiiFFFF',
            cmd_type,
            0xCAFE1234,
            self.calib['th_min'], self.calib['th_max'],
            self.calib['cl_min'], self.calib['cl_max'],
            self.calib['br_hall_min'], self.calib['br_hall_max'],
            int(self.calib['br_lc_min']), int(self.calib['br_lc_max']),
            self.calib['br_lc_trust'],
            int(self.calib['rs_min']), int(self.calib['rs_max']),
            int(self.calib['ls_min']), int(self.calib['ls_max']),
            self.calib['gear_col_left'], self.calib['gear_col_right'],
            float(self.calib['gear_row_top']), float(self.calib['gear_row_bot'])
        )
        try:
            self.shared_sock.sendto(packed, (BOARD_IP, BOARD_PORT))
        except Exception as e:
            print(f"Error sending command: {e}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ProductionCalibrationUI()
    window.show()
    sys.exit(app.exec())